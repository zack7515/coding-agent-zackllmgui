# -*- coding: utf-8 -*-
"""附件轉文字：PDF、docx／odt／pptx，其餘當純文字。"""

import io
import re
import shutil
import subprocess
import zipfile
from pathlib import Path


def _pdf_text(data: bytes) -> str:
    """PDF 轉文字。先用系統的 pdftotext，沒有才退回 pypdf。"""
    exe = shutil.which("pdftotext")
    if exe:
        proc = subprocess.run([exe, "-layout", "-", "-"], input=data,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        if proc.returncode == 0:
            return proc.stdout.decode("utf-8", "replace")
    try:
        import pypdf
    except ImportError:
        raise RuntimeError(
            "這台機器沒有 PDF 解析工具。二選一：\n"
            "  sudo apt install poppler-utils   （Windows：choco install poppler）\n"
            "  pip install pypdf") from None
    reader = pypdf.PdfReader(io.BytesIO(data))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


def _docx_text(data: bytes) -> str:
    """.docx / .pptx / .odt 都是 zip 裡的 XML，標籤拔掉就是文字。

    ponytail: 不做樣式與表格；要完整版面就換 python-docx。
    """
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = [n for n in z.namelist()
                 if n in ("word/document.xml", "content.xml")
                 or (n.startswith("ppt/slides/slide") and n.endswith(".xml"))]
        if not names:
            raise RuntimeError("這個 zip 裡沒有找到文件內容（不是 docx / odt / pptx？）")
        parts = []
        for name in sorted(names):
            xml = z.read(name).decode("utf-8", "replace")
            xml = re.sub(r"</w:p>|</text:p>|</a:p>", "\n", xml)
            xml = re.sub(r"<[^>]+>", "", xml)
            parts.append(xml)
    text = "".join(parts)
    text = (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&apos;", "'"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_text(filename: str, data: bytes) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext == ".pdf":
        return _pdf_text(data)
    if ext in (".docx", ".odt", ".pptx"):
        return _docx_text(data)
    return data.decode("utf-8", "replace")
