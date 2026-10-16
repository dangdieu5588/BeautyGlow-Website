<?php
namespace BeautyGlow\Products;

defined('ABSPATH') || exit;

function add_product_meta_box($post) {
    add_meta_box('beautyglow-product-fields', 'Thông tin sản phẩm và nguồn tham khảo', __NAMESPACE__ . '\\render_product_fields', 'bg_product', 'normal', 'high');
}

function render_product_fields($post) {
    if (!current_user_can('manage_options') || !current_user_can('edit_post', $post->ID)) {
        return;
    }
    wp_nonce_field('bg_save_product', 'bg_product_nonce');
    echo '<p>' . esc_html__('Tên: ô tiêu đề. Chi tiết: trình soạn thảo. Mô tả ngắn: ô Tóm tắt. Hình đại diện: chọn từ Media Library.', 'beautyglow-products') . '</p>';
    foreach (field_schema() as $field => $definition) {
        $id = 'bg_' . $field;
        $value = get_post_meta($post->ID, '_bg_' . $field, true);
        echo '<p><label for="' . esc_attr($id) . '"><strong>' . esc_html($definition['label']) . '</strong></label><br>';
        echo '<input class="widefat" id="' . esc_attr($id) . '" name="bg_product[' . esc_attr($field) . ']" type="' . esc_attr($definition['type']) . '" value="' . esc_attr(is_string($value) ? $value : '') . '"';
        if ('url' === $definition['type']) {
            echo ' placeholder="https://…"';
        }
        echo '></p>';
    }
    echo '<p class="description">' . esc_html__('Ghi URL nguồn đã kiểm chứng, không nhập token hoặc liên kết chứa thông tin đăng nhập. Chưa có thông tin thì để trống; không tự bịa thông số hoặc giá.', 'beautyglow-products') . '</p>';
}

function save_product_fields($post_id, $post) {
    if ('bg_product' !== $post->post_type || wp_is_post_revision($post_id) || wp_is_post_autosave($post_id)
        || (defined('DOING_AUTOSAVE') && DOING_AUTOSAVE)) {
        return;
    }
    if (!current_user_can('manage_options') || !current_user_can('edit_post', $post_id)) {
        return;
    }
    if (!isset($_POST['bg_product_nonce']) || !is_string($_POST['bg_product_nonce'])
        || !wp_verify_nonce(sanitize_text_field(wp_unslash($_POST['bg_product_nonce'])), 'bg_save_product')) {
        return;
    }
    // REST, quick edit, bulk edit or a missing meta box must not clear fields.
    if (!isset($_POST['bg_product']) || !is_array($_POST['bg_product'])) {
        return;
    }
    $input = wp_unslash($_POST['bg_product']);
    foreach (field_schema() as $field => $definition) {
        if (!array_key_exists($field, $input) || !is_string($input[$field])) {
            continue;
        }
        $value = sanitize_field($input[$field], $definition['type']);
        if ('' === $value) {
            delete_post_meta($post_id, '_bg_' . $field);
        } else {
            // Core strips slashes when saving metadata; preserve legitimate text.
            update_post_meta($post_id, '_bg_' . $field, wp_slash($value));
        }
    }
}

function product_columns($columns) {
    $columns['bg_code'] = 'Mã sản phẩm';
    $columns['bg_brand'] = 'Thương hiệu';
    return $columns;
}

function render_product_column($column, $post_id) {
    $keys = array('bg_code' => '_bg_code', 'bg_brand' => '_bg_brand');
    if (isset($keys[$column])) {
        $value = get_post_meta($post_id, $keys[$column], true);
        echo esc_html(is_string($value) ? $value : '');
    }
}
