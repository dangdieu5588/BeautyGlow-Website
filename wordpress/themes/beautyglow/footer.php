<?php defined('ABSPATH') || exit; ?>
</main>
<footer class="site-footer"><div class="container footer-grid">
    <div><a class="footer-brand" href="<?php echo esc_url(home_url('/')); ?>">BeautyGlow<span>✿</span></a><p>Một góc nhỏ để khám phá mỹ phẩm<br>và tìm cảm hứng chăm sóc bản thân.</p></div>
    <div><h2>Khám phá</h2><a href="<?php echo esc_url(beautyglow_catalog_url()); ?>">Tất cả sản phẩm</a><a href="<?php echo esc_url(beautyglow_info_url('gioi-thieu')); ?>">Câu chuyện BeautyGlow</a><a href="<?php echo esc_url(beautyglow_info_url('lien-he')); ?>">Liên hệ</a></div>
    <div><h2>Danh mục</h2><?php beautyglow_category_links(); ?></div>
</div><div class="container footer-bottom"><span>© <?php echo esc_html(wp_date('Y')); ?> BeautyGlow</span><span>Đồ án học tập · Website giới thiệu mỹ phẩm đa thương hiệu.<br>Không đại diện chính thức cho các thương hiệu.</span></div></footer>
<?php wp_footer(); ?></body></html>
