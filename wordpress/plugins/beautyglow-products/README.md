# BeautyGlow Products 0.1.0

Plugin quản trị danh mục mỹ phẩm, không phụ thuộc WooCommerce. Chưa nhập sản phẩm mẫu.

- CPT: `bg_product`; taxonomy: `bg_product_category`.
- Khi kích hoạt, tạo nếu chưa tồn tại: Chăm sóc da (`cham-soc-da`), Trang điểm
  (`trang-diem`), Chống nắng (`chong-nang`). Không tạo lại term sau mỗi request.
- Quyền thêm/sửa/xóa sản phẩm và danh mục yêu cầu `manage_options` (quản trị viên).
  Không thay đổi role hoặc capabilities đã lưu của người dùng.
- Tiêu đề, nội dung chi tiết, tóm tắt và ảnh đại diện dùng tính năng WordPress.
  Plugin dùng classic editor riêng cho sản phẩm để lưu các trường cùng form.
- Meta riêng: `_bg_brand`, `_bg_code`, `_bg_variant`, `_bg_size`,
  `_bg_information_source`, `_bg_image_source`. Trường nguồn nhận một URL HTTP(S).
  Không có giá, giỏ hàng, đơn hàng hoặc nhập liệu tự động.
- Trường trống được phép. Mã chưa có ràng buộc duy nhất; việc nhập 10 sản phẩm
  và xác minh phiên bản được thực hiện ở bước sau.
- Save handler kiểm tra post type, autosave/revision, quyền và nonce; làm sạch
  chuỗi/URL, bỏ qua kiểu dữ liệu bất hợp lệ và escape HTML khi hiển thị.
- Tắt plugin giữ nguyên sản phẩm, term, metadata và ảnh. Không có uninstall xóa dữ liệu.

## Áp dụng vào môi trường hiện tại

Cần phê duyệt riêng trước khi sửa Compose/recreate WordPress hoặc kích hoạt plugin.
Bind mount dự kiến chỉ đọc thư mục này vào
`/var/www/html/wp-content/plugins/beautyglow-products`; giữ nguyên WordPress volume.
Kích hoạt sẽ ghi các term mặc định, rewrite rules và trạng thái plugin trong database.
Không cần theme BeautyGlow để sử dụng trang quản trị. Chưa có template frontend riêng.

Sau khi đã mount và được phép kích hoạt: vào Plugins -> Installed Plugins ->
BeautyGlow Products -> Activate. Menu Sản phẩm xuất hiện; kiểm tra đúng ba danh mục.
Chưa thêm sản phẩm thử hoặc mẫu nếu chưa được duyệt ghi dữ liệu đó.
Trong trình sửa, dùng Tóm tắt cho mô tả ngắn; nếu bị ẩn, bật tại Tùy chọn hiển thị.
Ảnh đại diện sử dụng Media Library và giữ ảnh trong volume WordPress.

Kiểm tra thủ công ở bước được duyệt tiếp: thêm/sửa/xóa một sản phẩm thử, chọn ảnh,
xác nhận thông tin lưu lại, URL script bị loại và tài khoản không phải admin không
quản lý được sản phẩm. Kiểm tra archive/chi tiết theo permalink hiện tại, cả query
URL `?post_type=bg_product`; không tự đổi permalink hoặc cài plugin bổ sung.
