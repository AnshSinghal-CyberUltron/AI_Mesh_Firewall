"""P03: canonicalization evasion + engine parity (benchmarked source).

(a) Input: canonicalize() + Matcher on text where invisible/default-ignorable or combining code points are
    interleaved in a secret/PII.  The spec promises NFKC + zero-width/bidi stripping; Unicode's
    Default_Ignorable_Code_Point set is larger than rvproto's _CONTROLS list, and combining marks survive NFKC.
(b) Output: egress never canonicalizes (inspector.hits(raw text)), so even the obfuscations the INPUT side
    handles (fullwidth, ZWSP) pass on output.
(c) Engine parity: Hyperscan (units) vs Python re (fallback) on PII adjacent to non-ASCII letters.
"""
import sys, unicodedata
sys.path.insert(0, sys.argv[1])
from rvproto.detect.canon import canonicalize, _CONTROLS
from rvproto.detect.matcher import Matcher

KEY, SSN, MAIL = "AKIAQYLPMN5HHHFPZAM2", "123-45-6789", "alice.canary@example.com"
hs, rx = Matcher("hyperscan"), Matcher("re")

def interleave(s, ch):
    return ch.join(s)

obf = {
  "plain (control)": lambda s: s,
  "U+200B ZWSP (in list, control)": lambda s: interleave(s, "​"),
  "fullwidth (NFKC, control)": lambda s: s.translate({c: c + 0xFEE0 for c in range(0x21, 0x7F)}),
  "U+FE0F variation selector-16": lambda s: interleave(s, "️"),
  "U+E0020 TAG SPACE (tag block)": lambda s: interleave(s, "\U000E0020"),
  "U+034F combining grapheme joiner": lambda s: interleave(s, "͏"),
  "U+0301 combining acute after each char": lambda s: "".join(c + "́" for c in s),
  "U+061C arabic letter mark (bidi)": lambda s: interleave(s, "؜"),
  "U+180B mongolian FVS1": lambda s: interleave(s, "᠋"),
  "U+3164 hangul filler": lambda s: interleave(s, "ㅤ"),
  "U+2800 braille blank": lambda s: interleave(s, "⠀"),
  "U+206A inhibit symmetric swapping": lambda s: interleave(s, "⁪"),
}
ign = [c for c in range(0x110000) if unicodedata.category(chr(c)) in ("Cf", "Mn")] 
print("(a) INPUT side: canonicalize() then Matcher(hyperscan)")
print(f"{'obfuscation':<42} {'aws':>5} {'ssn':>5} {'email':>5}   (1 = detected)")
for name, f in obf.items():
    res = []
    for val, det in ((KEY, "secret.aws"), (SSN, "pii.ssn"), (MAIL, "pii.email")):
        t = f"value: {f(val)} end"
        c = canonicalize(t, 4096)
        res.append(int(any(h[0] == det for h in hs.scan(c.text))))
    print(f"{name:<42} {res[0]:>5} {res[1]:>5} {res[2]:>5}")

print("\n(b) OUTPUT side (egress scans raw text, no canonicalize): Matcher(hyperscan) on raw")
for name in ("plain (control)", "U+200B ZWSP (in list, control)", "fullwidth (NFKC, control)"):
    f = obf[name]
    res = [int(any(h[0] == det for h in hs.scan(f"value: {f(val)} end")))
           for val, det in ((KEY, "secret.aws"), (SSN, "pii.ssn"), (MAIL, "pii.email"))]
    print(f"{name:<42} {res[0]:>5} {res[1]:>5} {res[2]:>5}")

print("\n(c) engine parity (same canonical text): hyperscan vs re")
samples = {
  "CJK prefix, no space": "我的社保号是123-45-6789请保密",
  "CJK around email": "邮箱alice.canary@example.com谢谢",
  "accented letter glued": "réf123-45-6789",
  "Cyrillic glued AWS": "ключ" + KEY + "конец",
  "Devanagari glued card": "कार्ड4111111111111111धन्यवाद",
}
for name, t in samples.items():
    c = canonicalize(t, 4096)
    a = sorted({h[0] for h in hs.scan(c.text)})
    b = sorted({h[0] for h in rx.scan(c.text)})
    print(f"{name:<24} hyperscan={a!s:<28} re={b!s:<28} {'PARITY' if a == b else 'MISMATCH'}")
print("\n_CONTROLS size:", len(_CONTROLS), "; Unicode Cf+Mn code points:", len(ign))
