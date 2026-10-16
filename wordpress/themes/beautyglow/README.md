# BeautyGlow theme

Classic WordPress theme for the existing BeautyGlow Products plugin. Product
queries include published `bg_product` records only. Featured images come from
the Media Library; missing images use an explicit placeholder. No product data
is created or changed by the theme. Lotus artwork is original SVG artwork.

The default navigation works without creating WordPress menu records. An optional
custom menu can be assigned to the primary location in WordPress Admin.
Introduction and contact use `?bg_page=gioi-thieu` and `?bg_page=lien-he`, so no
page records or rewrite changes are required. Contact links to the project Issues
page; no fictional phone/email or nonfunctional submission form is supplied.

Deploy from Ubuntu, at `/mnt/d/BeautyGlow-Website`:

    python3 -B scripts/deploy-theme.py

The script lints PHP, copies only the theme into the existing WordPress volume,
activates it with `switch_theme`, and compares product/media record fingerprints.
It does not recreate containers or change Compose. Theme source is not a bind
mount; rerun this script after editing theme source. Normal theme activation can
update WordPress theme options; products, attachments and plugin settings are
not written by the script. To roll back the appearance, activate Twenty Twenty-Five
in Appearance > Themes; product/media records remain intact.

The catalog uses WordPress-generated archive, taxonomy and single URLs, including
the current plain permalink configuration. No cart, payment, external font or
third-party JavaScript dependency is included.

Headings use locally bundled Lora regular and italic variable fonts from
https://github.com/google/fonts/tree/main/ofl/lora, including Vietnamese glyphs.
The SIL Open Font License is preserved in assets/fonts/OFL-Lora.txt.
No Google Fonts request is made by visitors’ browsers.
