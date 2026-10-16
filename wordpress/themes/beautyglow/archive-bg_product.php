<?php defined('ABSPATH') || exit; get_header(); ?>
<section class="page-intro container"><p class="eyebrow">BỘ SƯU TẬP BEAUTYGLOW</p><h1><?php if (is_tax('bg_product_category')) { single_term_title(); } else { echo 'Khám phá sản phẩm'; } ?></h1><p>Những lựa chọn mỹ phẩm đa thương hiệu, dành cho hành trình chăm sóc bản thân.</p></section>
<section class="container catalog-section"><div class="catalog-toolbar"><div class="filter-list"><a class="filter-chip" href="<?php echo esc_url(beautyglow_catalog_url()); ?>">Tất cả</a><?php beautyglow_category_links(); ?></div><span><?php global $wp_query; echo esc_html($wp_query->found_posts); ?> sản phẩm</span></div>
<div class="product-grid"><?php if (have_posts()) { while (have_posts()) { the_post(); get_template_part('template-parts/product-card'); } } else { echo '<p>Chưa có sản phẩm trong danh mục này.</p>'; } ?></div>
<?php the_posts_pagination(array('prev_text' => '← Trước', 'next_text' => 'Sau →')); ?></section>
<?php get_footer(); ?>
