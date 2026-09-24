import zipfile, re, sys
from xml.etree import ElementTree as ET
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
z = zipfile.ZipFile(sys.argv[1])
root = ET.fromstring(z.read('word/document.xml'))
body = root.find(W+'body')
out = []
def para_text(p):
    t = []
    for node in p.iter():
        if node.tag == W+'t' and node.text: t.append(node.text)
        elif node.tag == W+'tab': t.append('\t')
        elif node.tag == W+'br': t.append('\n')
    return ''.join(t)
def style(p):
    ppr = p.find(W+'pPr')
    if ppr is None: return ''
    ps = ppr.find(W+'pStyle')
    return ps.get(W+'val') if ps is not None else ''
def numbered(p):
    ppr = p.find(W+'pPr')
    return ppr is not None and ppr.find(W+'numPr') is not None
for el in body:
    if el.tag == W+'p':
        s = style(el); txt = para_text(el)
        if not txt.strip(): out.append(''); continue
        m = re.match(r'Heading(\d)', s)
        if m: out.append('#'*int(m.group(1)) + ' ' + txt)
        elif numbered(el) or 'List' in s: out.append('- ' + txt)
        else: out.append(txt)
    elif el.tag == W+'tbl':
        out.append('[TABLE]')
        for tr in el.iter(W+'tr'):
            cells = []
            for tc in tr.findall(W+'tc'):
                cells.append(' '.join(para_text(p) for p in tc.iter(W+'p')).strip())
            out.append('| ' + ' | '.join(cells) + ' |')
        out.append('[/TABLE]')
print('\n'.join(out))
