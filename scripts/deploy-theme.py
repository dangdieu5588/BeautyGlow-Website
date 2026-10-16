"""Run in Ubuntu from the project root: python3 -B scripts/deploy-theme.py.

Deploys only the repository's BeautyGlow theme to the existing WordPress volume.
No container recreate, plugin change, product write or image import is performed.
"""
from pathlib import Path
import io
import json
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
THEME = ROOT / 'wordpress/themes/beautyglow'
CONTAINER = 'beautyglow-website-wordpress-1'

def docker(*args, data=None):
    result = subprocess.run(['docker', 'exec', '-i', CONTAINER, *args], input=data, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', errors='replace'))
    return result.stdout

def php(code):
    return docker('php', data=("<?php require '/var/www/html/wp-load.php';\n" + code).encode())

SNAPSHOT = r'''
$ids=get_posts(['post_type'=>['bg_product','attachment'],'post_status'=>'any','numberposts'=>-1,'fields'=>'ids','orderby'=>'ID','order'=>'ASC']);
$ids=array_merge($ids,get_posts(['post_type'=>'bg_product','post_status'=>'trash','numberposts'=>-1,'fields'=>'ids']));
sort($ids); $records=[]; $products=[];
foreach($ids as $id) {
    $p=get_post($id); $records[]=[(array)$p,get_post_meta($id),wp_get_object_terms($id,'bg_product_category',['fields'=>'ids'])];
    if($p->post_type==='bg_product') $products[]=['id'=>$id,'code'=>get_post_meta($id,'_bg_code',true),'status'=>$p->post_status,'image'=>get_post_thumbnail_id($id)];
}
echo json_encode(['hash'=>hash('sha256',serialize($records)),'products'=>$products,'theme'=>get_stylesheet()]);
'''

for path in sorted(THEME.rglob('*.php')):
    output = docker('php', '-l', data=path.read_bytes()).decode().strip()
    print(path.relative_to(ROOT), output)

before = json.loads(php(SNAPSHOT))
samples = [p for p in before['products'] if p['code'] in {f'BG{i:03}' for i in range(1, 11)}]
assert len(samples) == 10 and len({p['code'] for p in samples}) == 10
assert all(p['status'] == 'publish' for p in samples), 'Expected ten already published products; no products changed.'
print('Before:', json.dumps(before, ensure_ascii=False))

archive = io.BytesIO()
with tarfile.open(fileobj=archive, mode='w') as tar:
    for path in sorted(THEME.rglob('*')):
        if path.is_file() and path.name != '.gitkeep':
            tar.add(path, arcname='beautyglow/' + path.relative_to(THEME).as_posix())
docker('tar', '-xf', '-', '-C', '/var/www/html/wp-content/themes', data=archive.getvalue())
activation = php(r'''
$theme=wp_get_theme('beautyglow');
if(!$theme->exists() || $theme->errors()) { fwrite(STDERR,'Invalid theme'); exit(1); }
if(get_stylesheet()!=='beautyglow') switch_theme('beautyglow');
echo json_encode(['active'=>get_stylesheet(),'archive'=>get_post_type_archive_link('bg_product')]);
''')
print('Activation:', activation.decode())
after = json.loads(php(SNAPSHOT))
assert before['hash'] == after['hash'], 'Product or media data changed: investigate before continuing.'
assert after['theme'] == 'beautyglow'
print('PASS: active theme beautyglow; all product and media records/metadata unchanged.')
