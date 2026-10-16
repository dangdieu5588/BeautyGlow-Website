<?php
namespace BeautyGlow\Products;

defined('ABSPATH') || exit;

/** Field schema shared by validation, storage and the administration UI. */
function field_schema() {
    return array(
        'brand' => array('label' => 'Thương hiệu', 'type' => 'text'),
        'code' => array('label' => 'Mã sản phẩm', 'type' => 'text'),
        'variant' => array('label' => 'Phiên bản / màu', 'type' => 'text'),
        'size' => array('label' => 'Dung tích / khối lượng', 'type' => 'text'),
        'information_source' => array('label' => 'Nguồn thông tin sản phẩm', 'type' => 'url'),
        'image_source' => array('label' => 'Nguồn hình ảnh', 'type' => 'url'),
    );
}

/** Do not persist arrays, HTML, script URLs or arbitrary metadata keys. */
function sanitize_field($value, $type = 'text') {
    if (!is_string($value)) {
        return '';
    }
    return 'url' === $type ? esc_url_raw($value, array('http', 'https')) : sanitize_text_field($value);
}

function register_content() {
    // Product writes are reserved to administrators; public reads remain available.
    $capabilities = array_fill_keys(array(
        'edit_post', 'delete_post', 'edit_posts', 'edit_others_posts',
        'publish_posts', 'read_private_posts', 'delete_posts',
        'delete_private_posts', 'delete_published_posts', 'delete_others_posts',
        'edit_private_posts', 'edit_published_posts', 'create_posts',
    ), 'manage_options');
    // Published products are public, but private/draft reads require an admin.
    $capabilities['read_post'] = 'manage_options';

    register_post_type('bg_product', array(
        'labels' => array(
            'name' => 'Sản phẩm', 'singular_name' => 'Sản phẩm',
            'menu_name' => 'Sản phẩm', 'all_items' => 'Tất cả sản phẩm',
            'add_new' => 'Thêm sản phẩm', 'add_new_item' => 'Thêm sản phẩm mới',
            'edit_item' => 'Sửa sản phẩm', 'new_item' => 'Sản phẩm mới',
            'view_item' => 'Xem sản phẩm', 'search_items' => 'Tìm sản phẩm',
            'not_found' => 'Chưa có sản phẩm.',
            'not_found_in_trash' => 'Không có sản phẩm trong thùng rác.',
            'featured_image' => 'Hình ảnh đại diện',
            'set_featured_image' => 'Chọn hình ảnh từ thư viện',
            'remove_featured_image' => 'Gỡ hình ảnh đại diện',
            'use_featured_image' => 'Dùng làm hình ảnh đại diện',
        ),
        'public' => true,
        'show_in_rest' => true,
        'menu_icon' => 'dashicons-products',
        'has_archive' => 'san-pham',
        'rewrite' => array('slug' => 'san-pham', 'with_front' => false),
        'supports' => array('title', 'editor', 'excerpt', 'thumbnail', 'revisions'),
        'taxonomies' => array('bg_product_category'),
        'capabilities' => $capabilities,
        'map_meta_cap' => false,
        'delete_with_user' => false,
    ));

    register_taxonomy('bg_product_category', array('bg_product'), array(
        'labels' => array(
            'name' => 'Danh mục sản phẩm', 'singular_name' => 'Danh mục sản phẩm',
            'search_items' => 'Tìm danh mục', 'all_items' => 'Tất cả danh mục',
            'edit_item' => 'Sửa danh mục', 'update_item' => 'Cập nhật danh mục',
            'add_new_item' => 'Thêm danh mục mới', 'new_item_name' => 'Tên danh mục mới',
            'parent_item' => 'Danh mục cha', 'menu_name' => 'Danh mục',
        ),
        'public' => true,
        'hierarchical' => true,
        'show_in_rest' => true,
        'show_admin_column' => true,
        'rewrite' => array('slug' => 'danh-muc-san-pham', 'with_front' => false),
        'capabilities' => array_fill_keys(array('manage_terms', 'edit_terms', 'delete_terms', 'assign_terms'), 'manage_options'),
    ));

    foreach (field_schema() as $field => $definition) {
        register_post_meta('bg_product', '_bg_' . $field, array(
            'type' => 'string',
            'single' => true,
            'show_in_rest' => false,
            'sanitize_callback' => function ($value) use ($definition) {
                return sanitize_field($value, $definition['type']);
            },
            'auth_callback' => function ($allowed, $meta_key, $post_id) {
                return current_user_can('manage_options') && current_user_can('edit_post', $post_id);
            },
        ));
    }
}
