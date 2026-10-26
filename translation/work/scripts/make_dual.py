"""Step 4e: rebuild the side-by-side PDF from the original (left) and the final Chinese edition (right)."""
import sys
import pymupdf

src_path, zh_path, out_path = sys.argv[1:4]
src, zh = pymupdf.open(src_path), pymupdf.open(zh_path)
assert src.page_count == zh.page_count, (src.page_count, zh.page_count)
out = pymupdf.open()
for i in range(src.page_count):
    w, h = src[i].rect.width, src[i].rect.height
    page = out.new_page(width=2 * w, height=h)
    page.show_pdf_page(pymupdf.Rect(0, 0, w, h), src, i)
    page.show_pdf_page(pymupdf.Rect(w, 0, 2 * w, h), zh, i)
out.save(out_path, garbage=3, deflate=True)
print("wrote", out_path, out.page_count, "pages")
