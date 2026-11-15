"""의존성 없는 최소 DOCX 작성기(제목·문단·글머리·표·쪽나눔). 웹(브라우저 파이썬)에서도 동작.

Word·한글(HWP)에서 열리는 표준 OOXML(.docx)을 zip으로 직접 만든다.
"""
from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

FONT = "맑은 고딕"

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
</Types>"""
RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
</Relationships>"""
DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>
</Relationships>"""
SETTINGS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:defaultTabStop w:val="800"/></w:settings>"""


def _style(sid: str, name: str, size: int, bold: bool = False, color: str = "000000", before: int = 0, after: int = 120,
           outline: int | None = None, keep: bool = False) -> str:
    ol = f'<w:outlineLvl w:val="{outline}"/>' if outline is not None else ""
    kp = "<w:keepNext/>" if keep else ""
    return (f'<w:style w:type="paragraph" w:styleId="{sid}"><w:name w:val="{name}"/><w:basedOn w:val="Normal"/>'
            f'<w:qFormat/><w:pPr>{kp}<w:spacing w:before="{before}" w:after="{after}"/>{ol}</w:pPr>'
            f'<w:rPr>{"<w:b/>" if bold else ""}<w:color w:val="{color}"/><w:sz w:val="{size}"/></w:rPr></w:style>')


STYLES = ("""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="%s" w:hAnsi="%s" w:eastAsia="%s" w:cs="%s"/><w:sz w:val="20"/><w:lang w:eastAsia="ko-KR"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="80" w:line="300" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
""" % (FONT, FONT, FONT, FONT)
    + _style("Title", "Title", 40, True, "17485C", 1200, 240)
    + _style("Subtitle", "Subtitle", 24, False, "555555", 0, 600)
    + _style("Heading1", "heading 1", 30, True, "17485C", 360, 160, 0, True)
    + _style("Heading2", "heading 2", 24, True, "1F4E78", 240, 100, 1, True)
    + _style("Caption", "caption", 18, True, "444444", 120, 60, None, True)
    + _style("Small", "Small", 16, False, "666666", 0, 60)
    + """<w:style w:type="table" w:styleId="Grid"><w:name w:val="Table Grid"/><w:tblPr><w:tblBorders>
<w:top w:val="single" w:sz="4" w:color="999999"/><w:left w:val="single" w:sz="4" w:color="999999"/>
<w:bottom w:val="single" w:sz="4" w:color="999999"/><w:right w:val="single" w:sz="4" w:color="999999"/>
<w:insideH w:val="single" w:sz="4" w:color="999999"/><w:insideV w:val="single" w:sz="4" w:color="999999"/></w:tblBorders>
<w:tblCellMar><w:left w:w="80" w:type="dxa"/><w:right w:w="80" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>
</w:styles>""")


def _run(text: str, bold: bool = False, size: int | None = None, color: str | None = None) -> str:
    rpr = ("<w:b/>" if bold else "") + (f'<w:color w:val="{color}"/>' if color else "") + (f'<w:sz w:val="{size}"/>' if size else "")
    parts = str(text).split("\n")
    body = '<w:br/>'.join(f'<w:t xml:space="preserve">{escape(p)}</w:t>' for p in parts)
    return f"<w:r>{'<w:rPr>' + rpr + '</w:rPr>' if rpr else ''}{body}</w:r>"


class Doc:
    def __init__(self) -> None:
        self.body: list[str] = []

    def p(self, text="", style: str | None = None, bold=False, align: str | None = None, runs: list | None = None,
          size: int | None = None) -> "Doc":
        ppr = (f'<w:pStyle w:val="{style}"/>' if style else "") + (f'<w:jc w:val="{align}"/>' if align else "")
        rs = "".join(_run(t, b) for t, b in runs) if runs else _run(text, bold, size)
        self.body.append(f"<w:p>{'<w:pPr>' + ppr + '</w:pPr>' if ppr else ''}{rs}</w:p>")
        return self

    def h1(self, t): return self.p(t, "Heading1")
    def h2(self, t): return self.p(t, "Heading2")
    def caption(self, t): return self.p(t, "Caption")
    def small(self, t): return self.p(t, "Small")

    def bullets(self, items: list[str]) -> "Doc":
        for it in items:
            self.body.append(f'<w:p><w:pPr><w:ind w:left="400" w:hanging="200"/></w:pPr>{_run("· " + it)}</w:p>')
        return self

    def page_break(self) -> "Doc":
        self.body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
        return self

    def table(self, header: list[str], rows: list[list], widths: list[float] | None = None, font_size: int = 17,
              num_align_right: bool = True) -> "Doc":
        total = 9600   # 본문폭(twips) 약 17cm
        ws = [int(total * w / sum(widths)) for w in widths] if widths else [total // len(header)] * len(header)
        grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in ws)

        def cell(v, w, head=False):
            txt = "" if v is None or (isinstance(v, float) and v != v) else (f"{v:,.2f}".rstrip("0").rstrip(".") if isinstance(v, float)
                                                                             else f"{v:,}" if isinstance(v, int) and not isinstance(v, bool) else str(v))
            right = num_align_right and not head and isinstance(v, (int, float)) and not isinstance(v, bool)
            shade = '<w:shd w:val="clear" w:color="auto" w:fill="DDEBF7"/>' if head else ""
            jc = '<w:jc w:val="center"/>' if head else ('<w:jc w:val="right"/>' if right else "")
            return (f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/>{shade}<w:vAlign w:val="center"/></w:tcPr>'
                    f'<w:p><w:pPr><w:spacing w:after="0" w:line="260" w:lineRule="auto"/>{jc}</w:pPr>'
                    f'{_run(txt, head, font_size)}</w:p></w:tc>')
        trs = ['<w:tr><w:trPr><w:tblHeader/></w:trPr>' + "".join(cell(h, w, True) for h, w in zip(header, ws)) + "</w:tr>"]
        for r in rows:
            trs.append("<w:tr><w:trPr><w:cantSplit/></w:trPr>" + "".join(cell(v, w) for v, w in zip(r, ws)) + "</w:tr>")
        self.body.append(f'<w:tbl><w:tblPr><w:tblStyle w:val="Grid"/><w:tblW w:w="{total}" w:type="dxa"/>'
                         f'<w:tblLayout w:type="fixed"/></w:tblPr><w:tblGrid>{grid}</w:tblGrid>{"".join(trs)}</w:tbl>')
        self.body.append('<w:p><w:pPr><w:spacing w:after="60"/></w:pPr></w:p>')
        return self

    def to_bytes(self, title: str = "") -> bytes:
        doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
               + "".join(self.body)
               + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1418" w:right="1134" w:bottom="1418" '
                 'w:left="1134" w:header="851" w:footer="851" w:gutter="0"/></w:sectPr></w:body></w:document>')
        core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties '
                'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>' + escape(title) + '</dc:title>'
                '<dc:creator>물꼬</dc:creator></cp:coreProperties>')
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", CONTENT_TYPES)
            z.writestr("_rels/.rels", RELS)
            z.writestr("word/_rels/document.xml.rels", DOC_RELS)
            z.writestr("word/document.xml", doc)
            z.writestr("word/styles.xml", STYLES)
            z.writestr("word/settings.xml", SETTINGS)
            z.writestr("docProps/core.xml", core)
        return buf.getvalue()
