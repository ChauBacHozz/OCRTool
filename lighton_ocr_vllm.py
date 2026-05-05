import base64
import time
import re
import os
import io
import json
import gc
import shutil
from uuid import uuid4
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from openai import OpenAI
from PIL import Image
from bs4 import BeautifulSoup
from pdf2image import convert_from_path
from tqdm import tqdm


# ══════════════════════════════════════════════════════════════════════════════
# POST-PROCESSING: HTML → Markdown
# ══════════════════════════════════════════════════════════════════════════════

def _table_html_to_rows(table_elem) -> tuple:
    rows = table_elem.find_all("tr")
    has_th_header = False
    all_rows = []

    for i, row in enumerate(rows):
        ths = row.find_all("th")
        tds = row.find_all("td")
        if ths and i == 0:
            has_th_header = True
            all_rows.append([c.get_text(separator=" ", strip=True) for c in ths])
        else:
            cells = tds if tds else ths
            all_rows.append([c.get_text(separator=" ", strip=True) for c in cells])

    return has_th_header, all_rows


def _rows_to_md_table(rows: List[List[str]], has_header: bool = True) -> str:
    if not rows:
        return ""
    lines = []
    for i, row in enumerate(rows):
        lines.append("| " + " | ".join(row) + " |")
        if i == 0 and has_header:
            lines.append("| " + " | ".join(["---"] * len(row)) + " |")
    return "\n".join(lines)


def _html_to_markdown(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    def _process(node) -> str:
        tag = getattr(node, "name", None)
        if tag is None:
            return str(node).strip()
        if tag in ("script", "style"):
            return ""
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            return "#" * int(tag[1]) + " " + node.get_text(separator=" ", strip=True)
        elif tag == "p":
            return node.get_text(separator=" ", strip=True)
        elif tag == "br":
            return "\n"
        elif tag == "table":
            has_th, rows = _table_html_to_rows(node)
            return _rows_to_md_table(rows, has_header=has_th) if rows else ""
        elif tag == "ul":
            return "\n".join(
                "- " + li.get_text(separator=" ", strip=True)
                for li in node.find_all("li", recursive=False)
            )
        elif tag == "ol":
            return "\n".join(
                f"{i}. " + li.get_text(separator=" ", strip=True)
                for i, li in enumerate(node.find_all("li", recursive=False), 1)
            )
        elif tag in ("div", "section", "article", "figure", "body"):
            parts = [_process(child) for child in node.children]
            parts = [p.strip() for p in parts if p and p.strip()]
            return "\n\n".join(parts)
        elif tag in ("strong", "b"):
            return f"**{node.get_text(separator=' ', strip=True)}**"
        elif tag in ("em", "i"):
            return f"*{node.get_text(separator=' ', strip=True)}*"
        elif tag == "a":
            text = node.get_text(separator=" ", strip=True)
            href = node.get("href", "")
            return f"[{text}]({href})" if href else text
        elif tag == "img":
            return f"![{node.get('alt', 'image')}]({node.get('src', '')})"
        else:
            return node.get_text(separator=" ", strip=True)

    parts = [_process(child) for child in soup.children]
    parts = [p.strip() for p in parts if p and p.strip()]
    combined = "\n\n".join(parts)
    return re.sub(r'\n{3,}', '\n\n', combined).strip()


def _contains_html(text: str) -> bool:
    return bool(re.search(
        r"<(div|table|tr|td|th|h[1-6]|ul|ol|li|br|span|p|figure|section)\b",
        text, re.IGNORECASE
    ))


def _split_and_convert(text: str) -> str:
    if not text:
        return ""
    html_pattern = re.compile(
        r'(<(?:div|table|figure|section)[^>]*>.*?</(?:div|table|figure|section)>)',
        re.DOTALL | re.IGNORECASE
    )
    parts = html_pattern.split(text)
    result = []
    for part in parts:
        if not part.strip():
            continue
        if _contains_html(part):
            converted = _html_to_markdown(part)
            if converted.strip():
                result.append(converted.strip())
        else:
            result.append(part.strip())
    return re.sub(r'\n{3,}', '\n\n', "\n\n".join(result)).strip()


def _clean_ocr_artifacts(text: str) -> str:
    if not text:
        return ""

    text = re.sub(r'<BREAK\s*/?>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'</?\s*(?:div|span|br|hr|section|article)\s*/?>', '', text, flags=re.IGNORECASE)

    lines = text.splitlines()
    cleaned_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        repeat_count = 1
        while i + repeat_count < len(lines) and lines[i + repeat_count].strip() == line.strip():
            repeat_count += 1
        if repeat_count >= 4 and len(line.strip()) > 0:
            i += repeat_count
        else:
            cleaned_lines.append(line)
            i += 1
    text = "\n".join(cleaned_lines)

    def _remove_repeated_phrases(txt: str, min_words: int = 8, max_repeats: int = 3) -> str:
        paragraphs = re.split(r'\n{2,}', txt)
        from collections import Counter
        counts = Counter(p.strip() for p in paragraphs if len(p.split()) >= min_words)
        result_paragraphs = []
        seen_noisy = set()
        for p in paragraphs:
            key = p.strip()
            if counts.get(key, 0) > max_repeats:
                if key not in seen_noisy:
                    seen_noisy.add(key)
                    result_paragraphs.append(p)
            else:
                result_paragraphs.append(p)
        return "\n\n".join(result_paragraphs)

    text = _remove_repeated_phrases(text)

    lines = text.splitlines()
    text = "\n".join(
        line for line in lines
        if re.search(r'[a-zA-Z0-9\u00C0-\u024F\u0400-\u04FF\u4E00-\u9FFF\u0900-\u097F\uAC00-\uD7AF]', line)
        or line.strip() == ""
        or re.match(r'^\|[\s\-|:]+\|$', line.strip())
        or re.match(r'^-{3,}$', line.strip())
    )

    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _normalize_text(text: str) -> str:
    if not text:
        return ""
    if _contains_html(text):
        text = _split_and_convert(text)
    else:
        text = re.sub(r'\n{3,}', '\n\n', text)
    text = _clean_ocr_artifacts(text)
    return text.strip()


# ══════════════════════════════════════════════════════════════════════════════
# TABLE HEADER CONTINUATION
# ══════════════════════════════════════════════════════════════════════════════

def _get_last_table_header(md_text: str) -> Optional[List[str]]:
    table_blocks = re.findall(r'((?:\|[^\n]+\|\n?)+)', md_text)
    if not table_blocks:
        return None
    for block in reversed(table_blocks):
        lines = block.strip().splitlines()
        if len(lines) < 2:
            continue
        sep = lines[1].strip()
        if re.match(r'\|[\s\-|:]+\|', sep):
            header_line = lines[0].strip()
            cells = [c.strip() for c in header_line.split("|") if c.strip()]
            if cells:
                return cells
    return None


def _first_table_has_header(md_text: str) -> bool:
    match = re.search(r'((?:\|[^\n]+\|\n?)+)', md_text)
    if not match:
        return True
    lines = match.group(1).strip().splitlines()
    if len(lines) < 2:
        return False
    sep = lines[1].strip()
    return bool(re.match(r'\|[\s\-|:]+\|', sep))


def _inject_header_into_first_table(md_text: str, header: List[str]) -> str:
    match = re.search(r'((?:\|[^\n]+\|\n?)+)', md_text)
    if not match:
        return md_text
    original_block = match.group(1)
    header_row = "| " + " | ".join(header) + " |"
    sep_row    = "| " + " | ".join(["---"] * len(header)) + " |"
    new_block  = header_row + "\n" + sep_row + "\n" + original_block.strip()
    return md_text[:match.start()] + new_block + md_text[match.end():]


def stitch_table_headers(pages: List[str]) -> List[str]:
    if not pages:
        return pages
    result = list(pages)
    last_seen_header: Optional[List[str]] = None
    for i, page in enumerate(result):
        if not page:
            continue
        has_any_table = bool(re.search(r'(?:\|[^\n]+\|\n?)', page))
        if not has_any_table:
            continue
        if _first_table_has_header(page):
            new_header = _get_last_table_header(page)
            if new_header:
                last_seen_header = new_header
        else:
            if last_seen_header is not None:
                result[i] = _inject_header_into_first_table(page, last_seen_header)
                print(f"  🔗 Gán header cho bảng trang {i+1}: {last_seen_header}")
                new_header = _get_last_table_header(result[i])
                if new_header:
                    last_seen_header = new_header
    return result


# ══════════════════════════════════════════════════════════════════════════════
# LIGHTON OCR CLIENT (Thread-based, sync OpenAI client)
# ══════════════════════════════════════════════════════════════════════════════

class LightOnOCRClient:
    """
    Thread-based client for LightOnOCR-2-1B via vLLM server.
    """

    def __init__(self, base_url: str = "http://localhost:8000", max_workers: int = 8):
        self.base_url = base_url
        self.max_workers = max_workers
        self.model_name = "lightonai/LightOnOCR-2-1B"

    def _make_client(self) -> OpenAI:
        return OpenAI(
            api_key="EMPTY",
            base_url=f"{self.base_url}/v1",
            timeout=3600.0,
            max_retries=0,
        )

    def image_path_to_base64_url(self, image_path: str) -> str:
        """Đọc ảnh từ đĩa, resize và encode base64."""
        with Image.open(image_path) as image:
            max_side = 1540
            w, h = image.size
            temp_img = image
            if max(w, h) > max_side:
                scale = max_side / max(w, h)
                temp_img = image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

            if temp_img.mode != "RGB":
                temp_img = temp_img.convert("RGB")

            buffer = io.BytesIO()
            temp_img.save(buffer, format="JPEG", quality=85)
            base64_str = base64.b64encode(buffer.getvalue()).decode()
            
            # Giải phóng temp_img nếu nó là bản copy
            if temp_img != image:
                temp_img.close()
                
        return "data:image/png;base64," + base64_str

    def ocr_single_sync(
        self,
        image_path: str,
        prompt: str = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        top_p: float = 0.9,
    ) -> Dict:
        client = self._make_client()
        image_url = self.image_path_to_base64_url(image_path)

        content = [{"type": "image_url", "image_url": {"url": image_url}}]
        if prompt:
            content.append({"type": "text", "text": prompt})

        start = time.time()
        response = client.chat.completions.create(
            model=self.model_name,
            messages=[{"role": "user", "content": content}],
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
        )
        return {
            "text": response.choices[0].message.content,
            "time": time.time() - start,
        }

    def _ocr_single_with_retry(
        self,
        img_path: str,
        img_id: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        top_p: float,
        max_retries: int = 5,
        retry_delay: float = 3.0,
    ) -> Dict:
        last_error = None
        for attempt in range(max_retries):
            try:
                result = self.ocr_single_sync(
                    img_path,
                    prompt=prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    top_p=top_p,
                )
                return {
                    "image_id": img_id,
                    "text":     result["text"],
                    "time":     result["time"],
                    "error":    None,
                }
            except Exception as e:
                last_error = str(e)
                if attempt < max_retries - 1:
                    wait = min(retry_delay * (2 ** attempt), 60.0)
                    print(f"\n   ⚠️  {img_id} attempt {attempt + 1} failed: {last_error}")
                    time.sleep(wait)

        return {
            "image_id": img_id,
            "text":     None,
            "time":     0,
            "error":    last_error,
        }

    def ocr_batch(
        self,
        image_paths: List[str],
        image_ids: Optional[List[str]] = None,
        prompt: str = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        top_p: float = 0.9,
        max_retries: int = 5,
        retry_delay: float = 3.0,
    ) -> List[Dict]:
        if image_ids is None:
            image_ids = [f"img_{i}" for i in range(len(image_paths))]

        results_map: Dict[str, Dict] = {}
        print(f"   Workers    : {self.max_workers}  |  Total pages: {len(image_paths)}")

        # Sử dụng ThreadPoolExecutor để xử lý ảnh song song nhưng chỉ nạp base64 vào RAM khi cần
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_id = {
                executor.submit(
                    self._ocr_single_with_retry,
                    path, img_id,
                    prompt, max_tokens, temperature, top_p,
                    max_retries, retry_delay,
                ): img_id
                for path, img_id in zip(image_paths, image_ids)
            }

            with tqdm(total=len(image_paths), desc="🔄 OCR", unit="page") as pbar:
                for future in as_completed(future_to_id):
                    result = future.result()
                    results_map[result["image_id"]] = result
                    pbar.update(1)

        return [results_map[img_id] for img_id in image_ids]


# ══════════════════════════════════════════════════════════════════════════════
# PDF OCR PIPELINE (sync)
# ══════════════════════════════════════════════════════════════════════════════

class PDFOCRPipeline:
    def __init__(self, vllm_url: str = "http://localhost:8000", max_workers: int = 8):
        self.client = LightOnOCRClient(vllm_url, max_workers=max_workers)
        self.vllm_url = vllm_url

    def process_pdf(
        self,
        pdf_path: str,
        dpi: int = 200,
        prompt: str = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        top_p: float = 0.9,
        max_retries: int = 5,
        retry_delay: float = 3.0,
        output_dir: Optional[str] = None,
        save_images: bool = False,
        save_txt: bool = True,
        save_full_text: bool = False,
    ) -> Dict:
        pdf_name  = Path(pdf_path).stem
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Luôn tạo thư mục tạm để chứa ảnh convert từ PDF
        temp_img_dir = f"temp_ocr_{uuid4().hex}"
        os.makedirs(temp_img_dir, exist_ok=True)

        if output_dir is None and (save_images or save_txt or save_full_text):
            output_dir = f"ocr_output/{pdf_name}_{timestamp}"
            os.makedirs(output_dir, exist_ok=True)

        print(f"\n{'='*80}")
        print(f"📄 Processing PDF: {pdf_path}")
        print(f"    Server: {self.vllm_url}  |  Workers: {self.client.max_workers}")
        print(f"{'='*80}\n")

        # Step 1: PDF → images (Lưu trực tiếp vào đĩa)
        t0_conv = time.time()
        print(f"📄 Converting PDF to images (disk-based)...")
        # convert_from_path với output_folder sẽ lưu ảnh và trả về list đối tượng PIL (nhưng lazy)
        # Tuy nhiên để chắc chắn không tốn RAM, ta sẽ dùng paths_only=True
        image_paths = convert_from_path(
            pdf_path, 
            dpi=dpi, 
            output_folder=temp_img_dir, 
            fmt="png", 
            paths_only=True
        )
        convert_time = time.time() - t0_conv
        total_pages = len(image_paths)
        print(f"✔ Converted {total_pages} pages to {temp_img_dir} in {convert_time:.2f}s\n")

        # Step 2: Batch OCR (Truyền đường dẫn ảnh)
        print(f"🔄 Running OCR (max_workers={self.client.max_workers})...")
        page_ids = [f"page_{i+1}" for i in range(total_pages)]
        
        t0_ocr = time.time()
        results = self.client.ocr_batch(
            image_paths,
            image_ids=page_ids,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )
        total_ocr_time = time.time() - t0_ocr
        print(f"✅ Batch OCR finished in {total_ocr_time:.2f}s")

        # Step 3: Normalize
        print("🔧 Normalizing output...")
        successful = [r for r in results if r["error"] is None]
        failed     = [r for r in results if r["error"] is not None]
        normalized = [_normalize_text(r["text"]) for r in successful if r["text"]]
        
        # Step 4: Stitch tables
        print("🔗 Checking for split tables...")
        normalized = stitch_table_headers(normalized)

        # Step 5: Save results
        if save_txt and output_dir:
            pages_dir = os.path.join(output_dir, "pages")
            os.makedirs(pages_dir, exist_ok=True)
            for i, r in enumerate(successful):
                if i < len(normalized):
                    page_file = os.path.join(pages_dir, f"{r['image_id']}.txt")
                    with open(page_file, "w", encoding="utf-8") as f:
                        f.write(normalized[i])

        if save_full_text and output_dir:
            full_text_file = os.path.join(output_dir, "full_text.md")
            with open(full_text_file, "w", encoding="utf-8") as f:
                f.write("\n\n".join(normalized))

        avg_time = sum(r["time"] for r in results if r["time"] > 0) / len(successful) if successful else 0
        stats = {
            "total_pages": total_pages,
            "successful": len(successful),
            "failed": len(failed),
            "ocr_time": round(total_ocr_time, 2),
            "avg_per_page": round(avg_time, 2),
            "total_time": round(convert_time + total_ocr_time, 2),
        }

        print(f"\n📊 Summary: {stats['successful']}/{stats['total_pages']} pages successful.")

        # Cleanup: Xóa thư mục ảnh tạm
        print(f"🧹 Cleaning up temporary images in {temp_img_dir}...")
        try:
            shutil.rmtree(temp_img_dir)
        except Exception as e:
            print(f"⚠️ Warning: Could not remove temp dir {temp_img_dir}: {e}")
            
        gc.collect()

        return {
            "pages": results,
            "full_text": normalized,
            "output_dir": output_dir,
            "stats": stats,
        }

