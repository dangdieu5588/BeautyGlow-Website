<?php
defined('ABSPATH') || exit;

function beautyglow_setup() {
    add_theme_support('title-tag');
    add_theme_support('post-thumbnails');
    add_theme_support('html5', array('search-form', 'gallery', 'caption', 'style', 'script'));
    register_nav_menus(array('primary' => 'Điều hướng chính'));
}
add_action('after_setup_theme', 'beautyglow_setup');

add_action('wp_enqueue_scripts', function () {
    wp_enqueue_style('beautyglow', get_stylesheet_uri(), array(), filemtime(get_template_directory() . '/style.css'));
    wp_enqueue_script('beautyglow', get_template_directory_uri() . '/assets/navigation.js', array(), filemtime(get_template_directory() . '/assets/navigation.js'), true);
});

function beautyglow_info_url($page) {
    return add_query_arg('bg_page', $page, home_url('/'));
}
function beautyglow_catalog_url() {
    return get_post_type_archive_link('bg_product');
}
add_filter('query_vars', function ($vars) { $vars[] = 'bg_page'; return $vars; });
add_filter('template_include', function ($template) {
    if (in_array(get_query_var('bg_page'), array('gioi-thieu', 'lien-he'), true)) {
        return get_template_directory() . '/information.php';
    }
    return $template;
});
add_filter('pre_get_document_title', function ($title) {
    $page = get_query_var('bg_page');
    if ($page === 'gioi-thieu') { return 'Giới thiệu — BeautyGlow'; }
    if ($page === 'lien-he') { return 'Liên hệ — BeautyGlow'; }
    if (is_front_page()) { return 'BeautyGlow — Mỹ phẩm & Chăm sóc sắc đẹp'; }
    return $title;
});
add_action('pre_get_posts', function ($query) {
    if (!is_admin() && $query->is_main_query()) {
        if ($query->is_search()) { $query->set('post_type', 'bg_product'); }
        if ($query->is_search() || $query->is_post_type_archive('bg_product') || $query->is_tax('bg_product_category')) {
            $query->set('posts_per_page', 12);
            $query->set('post_status', 'publish');
            if (!$query->is_search()) {
                $query->set('orderby', 'ID');
                $query->set('order', 'ASC');
            }
        }
    }
});

function beautyglow_brands() {
    $brands = array();
    $ids = get_posts(array('post_type' => 'bg_product', 'post_status' => 'publish', 'numberposts' => -1, 'fields' => 'ids', 'orderby' => 'ID', 'order' => 'ASC'));
    foreach ($ids as $id) {
        $brand = get_post_meta($id, '_bg_brand', true);
        if ($brand !== '' && !in_array($brand, $brands, true)) { $brands[] = $brand; }
    }
    echo esc_html(implode('  ·  ', $brands));
}

function beautyglow_search_form() { ?>
    <form class="search-form" role="search" method="get" action="<?php echo esc_url(home_url('/')); ?>">
        <label class="screen-reader-text" for="bg-search">Tìm kiếm sản phẩm</label>
        <input id="bg-search" type="search" name="s" value="<?php echo esc_attr(get_search_query()); ?>" placeholder="Tìm sản phẩm…">
        <input type="hidden" name="post_type" value="bg_product">
        <button type="submit" aria-label="Tìm kiếm">⌕</button>
    </form>
<?php }

function beautyglow_categories() {
    return get_terms(array('taxonomy' => 'bg_product_category', 'hide_empty' => false, 'orderby' => 'name'));
}
function beautyglow_category_links() {
    $terms = beautyglow_categories();
    if (is_wp_error($terms)) { return; }
    foreach ($terms as $term) {
        $url = get_term_link($term);
        if (!is_wp_error($url)) {
            echo '<a class="filter-chip" href="' . esc_url($url) . '">' . esc_html($term->name) . '</a>';
        }
    }
}
