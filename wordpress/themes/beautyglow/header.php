<?php defined('ABSPATH') || exit; ?><!doctype html>
<html <?php language_attributes(); ?>>
<head><meta charset="<?php bloginfo('charset'); ?>"><meta name="viewport" content="width=device-width, initial-scale=1"><?php wp_head(); ?></head>
<body <?php body_class(); ?>><?php wp_body_open(); ?>
<a class="skip-link" href="#main-content">Bỏ qua điều hướng</a>
<div class="announcement">Vẻ đẹp dịu dàng · Cảm hứng từ thiên nhiên</div>
<header class="site-header">
    <div class="container header-inner">
        <a class="brand" href="<?php echo esc_url(home_url('/')); ?>" aria-label="BeautyGlow — Trang chủ"><span class="brand-mark" aria-hidden="true">✿</span><span>Beauty<span class="brand-glow">Glow</span><small>MỸ PHẨM & CHĂM SÓC SẮC ĐẸP</small></span></a>
        <button class="menu-toggle" aria-expanded="false" aria-controls="primary-navigation">☰ <span>Menu</span></button>
        <nav id="primary-navigation" class="primary-navigation" aria-label="Điều hướng chính">
            <?php if (has_nav_menu('primary')) {
                wp_nav_menu(array('theme_location' => 'primary', 'container' => false, 'menu_class' => 'nav-links', 'depth' => 1));
            } else { ?>
            <a href="<?php echo esc_url(home_url('/')); ?>">Trang chủ</a>
            <a href="<?php echo esc_url(beautyglow_catalog_url()); ?>">Sản phẩm</a>
            <a href="<?php echo esc_url(beautyglow_info_url('gioi-thieu')); ?>">Giới thiệu</a>
            <a href="<?php echo esc_url(beautyglow_info_url('lien-he')); ?>">Liên hệ</a>
            <?php } ?>
        </nav>
        <?php beautyglow_search_form(); ?>
    </div>
</header>
<main id="main-content">
