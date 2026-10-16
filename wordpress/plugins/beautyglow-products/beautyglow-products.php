<?php
/**
 * Plugin Name: BeautyGlow Products
 * Description: Quản lý sản phẩm mỹ phẩm và nguồn tham khảo cho website BeautyGlow.
 * Version: 0.1.0
 * Requires at least: 6.5
 * Requires PHP: 8.0
 * Text Domain: beautyglow-products
 */

namespace BeautyGlow\Products;

defined('ABSPATH') || exit;

require_once __DIR__ . '/includes/content.php';
require_once __DIR__ . '/includes/admin.php';

add_action('init', __NAMESPACE__ . '\\register_content');
add_action('add_meta_boxes_bg_product', __NAMESPACE__ . '\\add_product_meta_box');
add_action('save_post_bg_product', __NAMESPACE__ . '\\save_product_fields', 10, 2);
add_filter('manage_bg_product_posts_columns', __NAMESPACE__ . '\\product_columns');
add_action('manage_bg_product_posts_custom_column', __NAMESPACE__ . '\\render_product_column', 10, 2);

/** Keep the native media/featured-image UI available with the current theme. */
function enable_product_thumbnails() {
    if (!current_theme_supports('post-thumbnails')) {
        add_theme_support('post-thumbnails', array('bg_product'));
    } elseif (!current_theme_supports('post-thumbnails', 'bg_product')) {
        $support = get_theme_support('post-thumbnails');
        if (is_array($support) && isset($support[0]) && is_array($support[0])) {
            add_theme_support('post-thumbnails', array_unique(array_merge($support[0], array('bg_product'))));
        }
    }
}
add_action('after_setup_theme', __NAMESPACE__ . '\\enable_product_thumbnails', 20);

/** Use the native classic editor so product fields are submitted together. */
function use_product_editor($use_block_editor, $post_type) {
    return 'bg_product' === $post_type ? false : $use_block_editor;
}
add_filter('use_block_editor_for_post_type', __NAMESPACE__ . '\\use_product_editor', 10, 2);

/** Activation writes only the three default terms and rewrite rules. No products. */
function activate() {
    if (!current_user_can('activate_plugins') || !current_user_can('manage_options')) {
        wp_die(esc_html__('Bạn không có quyền kích hoạt plugin này.', 'beautyglow-products'));
    }
    register_content();
    foreach (array(
        'cham-soc-da' => 'Chăm sóc da',
        'trang-diem' => 'Trang điểm',
        'chong-nang' => 'Chống nắng',
    ) as $slug => $name) {
        if (!term_exists($slug, 'bg_product_category')) {
            $result = wp_insert_term($name, 'bg_product_category', array('slug' => $slug));
            if (is_wp_error($result)) {
                wp_die(esc_html__('Không tạo được danh mục sản phẩm. Vui lòng kiểm tra database rồi kích hoạt lại.', 'beautyglow-products'));
            }
        }
    }
    flush_rewrite_rules(false);
}
register_activation_hook(__FILE__, __NAMESPACE__ . '\\activate');

/** Deactivation preserves every product, term, attachment and metadata value. */
function deactivate() {
    unregister_taxonomy('bg_product_category');
    unregister_post_type('bg_product');
    flush_rewrite_rules(false);
}
register_deactivation_hook(__FILE__, __NAMESPACE__ . '\\deactivate');
