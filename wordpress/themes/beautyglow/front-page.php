<?php defined('ABSPATH') || exit; get_header(); ?>
<section class="hero"><div class="container hero-grid"><div class="hero-copy">
    <p class="eyebrow">BEAUTYGLOW · VẺ ĐẸP TỰ NHIÊN</p>
    <h1>Dịu dàng như sen.<br><em>Rạng rỡ là bạn.</em></h1>
    <p>Khám phá thế giới mỹ phẩm và chăm sóc sắc đẹp trong một không gian nhẹ nhàng, lấy cảm hứng từ sắc sen hồng và mặt hồ xanh.</p>
    <div class="hero-actions"><a class="button" href="<?php echo esc_url(beautyglow_catalog_url()); ?>">Khám phá sản phẩm <span aria-hidden="true">↗</span></a><a class="quiet-link" href="<?php echo esc_url(beautyglow_info_url('gioi-thieu')); ?>">Về BeautyGlow →</a></div>
    <div class="hero-note"><span aria-hidden="true">✧</span> Chăm chút bản thân, theo cách của bạn.</div>
</div><div class="hero-art"><img src="<?php echo esc_url(get_template_directory_uri() . '/assets/lotus-pond.svg'); ?>" alt="Minh họa hoa sen hồng và lá sen trên mặt hồ xanh" width="720" height="700" fetchpriority="high"><div class="art-caption"><span>THE LOTUS EDIT</span><small>Một chút bình yên. Một chút rạng ngời.</small></div></div></div></section>
<div class="brand-strip container"><span>CẢM HỨNG ĐA THƯƠNG HIỆU</span><p><?php beautyglow_brands(); ?></p></div>
<section class="section container"><div class="section-heading"><div><p class="eyebrow">TÌM ĐIỀU BẠN YÊU</p><h2>Góc chăm sóc sắc đẹp</h2></div><p>Từ những bước chăm da đến điểm nhấn trang điểm,<br>khám phá từng danh mục theo sở thích của bạn.</p></div>
<div class="category-grid"><?php
$decor = array('cham-soc-da' => array('01', 'Chăm chút mỗi ngày', 'leaf'), 'trang-diem' => array('02', 'Điểm tô nét riêng', 'pink'), 'chong-nang' => array('03', 'Đồng hành cùng nắng', 'cream'));
$terms = beautyglow_categories();
if (!is_wp_error($terms)) { foreach ($terms as $term) { $d = $decor[$term->slug] ?? array('✿', 'Khám phá thêm', 'leaf'); $url = get_term_link($term); if (is_wp_error($url)) { continue; } ?>
<a class="category-card <?php echo esc_attr($d[2]); ?>" href="<?php echo esc_url($url); ?>"><span class="category-number"><?php echo esc_html($d[0]); ?></span><span class="category-flower" aria-hidden="true">✿</span><div><small><?php echo esc_html($d[1]); ?></small><h3><?php echo esc_html($term->name); ?></h3></div><span class="category-arrow" aria-hidden="true">↗</span></a>
<?php } } ?></div></section>
<section class="section collection-section"><div class="container"><div class="section-heading"><div><p class="eyebrow">BEAUTYGLOW PICKS</p><h2>Một chút cảm hứng cho bạn</h2></div><a class="text-link" href="<?php echo esc_url(beautyglow_catalog_url()); ?>">Xem tất cả sản phẩm →</a></div>
<div class="product-grid"><?php $products = new WP_Query(array('post_type' => 'bg_product', 'post_status' => 'publish', 'posts_per_page' => 4, 'orderby' => 'ID', 'order' => 'ASC')); while ($products->have_posts()) { $products->the_post(); get_template_part('template-parts/product-card'); } wp_reset_postdata(); ?></div></div></section>
<section class="section container"><div class="about-panel"><div class="about-symbol" aria-hidden="true">✿<span>BEAUTY IN BLOOM</span></div><div><p class="eyebrow">CÂU CHUYỆN BEAUTYGLOW</p><h2>Vẻ đẹp bắt đầu từ<br>những điều giản dị.</h2><p>BeautyGlow là không gian giới thiệu mỹ phẩm đa thương hiệu, giúp bạn khám phá sản phẩm và tham khảo thông tin trong một trải nghiệm gọn gàng, dễ tìm hiểu.</p><a class="text-link" href="<?php echo esc_url(beautyglow_info_url('gioi-thieu')); ?>">Đọc câu chuyện của chúng mình →</a></div></div></section>
<section class="contact-band"><div class="container"><p class="eyebrow">KẾT NỐI CÙNG BEAUTYGLOW</p><h2>Cùng làm nên một góc đẹp hơn.</h2><p>Bạn có góp ý về sản phẩm hoặc nội dung website?<br>Chúng mình luôn trân trọng những chia sẻ của bạn.</p><a class="button button-light" href="<?php echo esc_url(beautyglow_info_url('lien-he')); ?>">Thông tin liên hệ ↗</a></div></section>
<?php get_footer(); ?>
