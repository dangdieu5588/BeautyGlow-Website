<?php defined('ABSPATH') || exit; ?>
<article class="product-card">
    <a class="product-photo" href="<?php the_permalink(); ?>" aria-label="<?php echo esc_attr(get_the_title()); ?>">
        <?php if (has_post_thumbnail()) { the_post_thumbnail('medium_large', array('loading' => 'lazy')); }
        else { ?><span class="image-placeholder"><span aria-hidden="true">✿</span>Chưa có ảnh</span><?php } ?>
        <span class="photo-arrow" aria-hidden="true">↗</span>
    </a>
    <div class="product-copy"><p class="product-brand"><?php echo esc_html(get_post_meta(get_the_ID(), '_bg_brand', true)); ?></p>
    <h3><a href="<?php the_permalink(); ?>"><?php the_title(); ?></a></h3>
    <p class="product-size"><?php echo esc_html(get_post_meta(get_the_ID(), '_bg_size', true)); ?></p>
    <a class="text-link" href="<?php the_permalink(); ?>">Khám phá sản phẩm <span aria-hidden="true">→</span></a></div>
</article>
