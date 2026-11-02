"""A plain .xlsx workbook (text and whole numbers, a bold frozen header row with
filters) written without any spreadsheet library."""
import io
import zipfile
from xml.sax.saxutils import escape

_TYPES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
          '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
          '{sheets}</Types>')
_SHEET_TYPE = ('<Override PartName="/xl/worksheets/sheet{n}.xml" '
               'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
         '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
         'Target="xl/workbook.xml"/></Relationships>')
_STYLES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
           '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
           '<borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs>'
           '<cellXfs count="2"><xf/><xf fontId="1" applyFont="1"/></cellXfs></styleSheet>')


def _column(index):
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def _cell(ref, value, style=0):
    if isinstance(value, int) and not isinstance(value, bool):
        return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
    return f'<c r="{ref}" t="inlineStr" s="{style}"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>'


def _sheet(header, rows, widths):
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
           '</sheetView></sheetViews><cols>']
    out += [f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths, 1)]
    out.append('</cols><sheetData><row r="1">')
    out += [_cell(f"{_column(i)}1", text, 1) for i, text in enumerate(header)]
    out.append("</row>")
    for n, row in enumerate(rows, 2):
        out.append(f'<row r="{n}">')
        out += [_cell(f"{_column(i)}{n}", value) for i, value in enumerate(row) if value not in (None, "")]
        out.append("</row>")
    out.append(f'</sheetData><autoFilter ref="A1:{_column(len(header) - 1)}{len(rows) + 1}"/></worksheet>')
    return "".join(out)


def workbook(sheets):
    """The .xlsx file's bytes for [(sheet name, header, rows, column widths)]."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _TYPES.format(sheets="".join(_SHEET_TYPE.format(n=n) for n in range(1, len(sheets) + 1))))
        z.writestr("_rels/.rels", _RELS)
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   + "".join(f'<sheet name="{escape(name[:31])}" sheetId="{n}" r:id="rId{n}"/>'
                             for n, (name, *_) in enumerate(sheets, 1))
                   + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{n}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                             f'Target="worksheets/sheet{n}.xml"/>' for n in range(1, len(sheets) + 1))
                   + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
                     'Target="styles.xml"/></Relationships>')
        z.writestr("xl/styles.xml", _STYLES)
        for n, (_, header, rows, widths) in enumerate(sheets, 1):
            z.writestr(f"xl/worksheets/sheet{n}.xml", _sheet(header, rows, widths))
    return buffer.getvalue()
