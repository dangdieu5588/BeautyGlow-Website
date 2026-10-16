<?php defined('ABSPATH') || exit; get_header(); ?>
<div class="container prose section"><?php if (have_posts()) { while (have_posts()) { the_post(); ?><article><h1><?php the_title(); ?></h1><?php the_content(); ?></article><?php } the_posts_pagination(); } else { ?><h1>Không tìm thấy trang</h1><p>Nội dung bạn tìm chưa có trên BeautyGlow.</p><a class="button" href="<?php echo esc_url(home_url('/')); ?>">Về trang chủ</a><?php } ?></div>
<?php get_footer(); ?>
