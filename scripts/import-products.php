<?php
/** CLI-only WordPress API import. JSON is injected by import-products.py. */
if (PHP_SAPI !== 'cli' || !defined('BG_SAMPLE_JSON')) {
    exit(1);
}
define('DISABLE_WP_CRON', true);
require '/var/www/html/wp-load.php';

function bg_import_assert($condition, $message) {
    if (!$condition) {
        throw new RuntimeException($message);
    }
}

try {
    bg_import_assert(post_type_exists('bg_product') && function_exists('BeautyGlow\\Products\\field_schema'), 'Plugin is not active.');
    bg_import_assert(get_option('home') === 'http://localhost:18080', 'Unexpected target website.');
    $data = json_decode(BG_SAMPLE_JSON, true, 512, JSON_THROW_ON_ERROR);
    bg_import_assert($data['batch'] === 'beautyglow-sample-products-v1', 'Unexpected import batch.');
    $rows = $data['products'];
    bg_import_assert(count($rows) === 10, 'Expected ten products.');
    $expected = array_map(function ($i) { return sprintf('BG%03d', $i); }, range(1, 10));
    bg_import_assert(array_column($rows, 'code') === $expected, 'Unexpected/duplicate product codes.');
    $admins = get_users(array('role' => 'administrator', 'number' => 1, 'fields' => 'ID'));
    bg_import_assert(!empty($admins), 'No existing administrator found.');
    wp_set_current_user((int) $admins[0]);
    bg_import_assert(current_user_can('manage_options'), 'Administrator permission required.');
    $fields = BeautyGlow\Products\field_schema();
    bg_import_assert(array_keys($fields) === array('brand', 'code', 'variant', 'size', 'information_source', 'image_source'), 'Plugin field schema changed.');

    // Serialize imports without changing options, roles, containers or volume data directly.
    global $wpdb;
    $locked = (string) $wpdb->get_var("SELECT GET_LOCK('beautyglow_sample_products_v1', 0)") === '1';
    bg_import_assert($locked, 'Another import is running; stopped.');
    $categories = array('cham-soc-da' => 0, 'trang-diem' => 0, 'chong-nang' => 0);
    $existing = array();
    foreach ($rows as $row) {
        bg_import_assert(isset($categories[$row['category']]), 'Unknown category.');
        ++$categories[$row['category']];
        bg_import_assert(get_term_by('slug', $row['category'], 'bg_product_category') !== false, 'Missing category.');
        bg_import_assert(!empty($row['title']) && !empty($row['brand']), 'Missing title/brand.');
        foreach ($fields as $key => $definition) {
            bg_import_assert(isset($row[$key]) && is_string($row[$key]), 'Invalid field ' . $key);
            bg_import_assert(BeautyGlow\Products\sanitize_field($row[$key], $definition['type']) === $row[$key], 'Unclean field ' . $key);
        }
        if (in_array($row['code'], array('BG004', 'BG009'), true)) {
            bg_import_assert($row['variant'] === '' && $row['size'] === '', 'Unconfirmed color/weight must stay empty.');
        }
        bg_import_assert($row['image_source'] === '', 'This batch does not import images.');
        $matches = get_posts(array(
            'post_type' => 'bg_product', 'post_status' => array_keys(get_post_stati()),
            'numberposts' => -1, 'meta_key' => '_bg_code', 'meta_value' => $row['code'],
        ));
        bg_import_assert(count($matches) <= 1, 'Duplicate existing code: ' . $row['code']);
        if ($matches) {
            bg_import_assert(get_post_meta($matches[0]->ID, '_bg_sample_batch', true) === $data['batch'], 'User-owned product code collision: ' . $row['code']);
            // Never overwrite edits, restore trash or republish an existing sample.
            $existing[$row['code']] = $matches[0]->ID;
        }
    }
    bg_import_assert($categories === array('cham-soc-da' => 6, 'trang-diem' => 3, 'chong-nang' => 1), 'Wrong category distribution.');
    $created = 0;
    $skipped = 0;
    $report = array();
    foreach ($rows as $row) {
        if (isset($existing[$row['code']])) {
            $id = $existing[$row['code']];
            ++$skipped;
        } else {
            $term = get_term_by('slug', $row['category'], 'bg_product_category');
            $meta = array('_bg_sample_batch' => $data['batch']);
            foreach ($fields as $key => $definition) {
                $meta['_bg_' . $key] = $row[$key];
            }
            $id = wp_insert_post(wp_slash(array(
                'post_type' => 'bg_product', 'post_status' => 'draft',
                'post_title' => $row['title'], 'post_name' => strtolower($row['code']),
                'post_excerpt' => $row['excerpt'], 'post_content' => $row['content'],
                'post_author' => (int) $admins[0], 'meta_input' => $meta,
                'tax_input' => array('bg_product_category' => array((int) $term->term_id)),
            )), true);
            bg_import_assert(!is_wp_error($id) && $id > 0, 'Insert failed for ' . $row['code']);
            ++$created;
        }
        $report[] = array(
            'code' => $row['code'], 'id' => $id, 'title' => get_the_title($id),
            'status' => get_post_status($id),
            'categories' => wp_get_object_terms($id, 'bg_product_category', array('fields' => 'names')),
            'has_image' => has_post_thumbnail($id),
            'edit_url' => get_edit_post_link($id, 'raw'),
        );
    }
    echo wp_json_encode(array('created' => $created, 'skipped' => $skipped, 'products' => $report), JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT), PHP_EOL;
} catch (Throwable $error) {
    fwrite(STDERR, 'IMPORT_STOPPED: ' . $error->getMessage() . PHP_EOL);
    exit(1);
} finally {
    if (!empty($locked)) {
        $wpdb->get_var("SELECT RELEASE_LOCK('beautyglow_sample_products_v1')");
    }
}
