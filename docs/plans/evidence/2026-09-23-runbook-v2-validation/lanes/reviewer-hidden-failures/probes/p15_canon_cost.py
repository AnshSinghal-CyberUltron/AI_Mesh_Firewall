"""P15: input-phase CPU the English-only benchmark never exercises (benchmarked canonicalize + Matcher + PG2 tokenizer).
The headline corpus is 100% ASCII English, so canonicalize() always takes the identity fast path (s.isascii()).
Any non-ASCII char (accent, CJK, emoji, curly quote) sends the WHOLE segment through the per-char Python loop."""
import sys, time
sys.path.insert(0, sys.argv[1])
from tokenizers import Tokenizer
from rvproto.detect.canon import canonicalize
from rvproto.detect.matcher import Matcher
tok = Tokenizer.from_file(sys.argv[2]); tok.no_truncation(); tok.no_padding()
m = Matcher()
base_en = "The committee thanked the volunteers who organised the charity run along the coast. "
samples = {
 "English ASCII (benchmark shape)": base_en,
 "English + one curly apostrophe": base_en.replace("organised", "organis’d"),
 "French (accents)": "Le comité a remercié les bénévoles qui ont organisé la course caritative le long de la côte. ",
 "German (umlauts)": "Das Komitee dankte den Freiwilligen, die den Wohltätigkeitslauf entlang der Küste organisiert hatten. ",
 "Chinese": "委员会感谢了组织沿海慈善跑的志愿者们，他们的努力让活动顺利完成。",
 "emoji-heavy chat": "great job team 🎉🎉 the run was amazing 🏃‍♀️🌊 thanks everyone 🙏 ",
}
def reps_to(text, n_tokens):
    t = text
    while len(tok.encode(t, add_special_tokens=False).ids) < n_tokens:
        t += text
    return t
print(f"{'input (~1,000 PG2 tokens)':<34} {'chars':>6} {'canonicalize':>13} {'matcher':>9} {'tokenize':>9}   (median of 20, ms)")
for name, s in samples.items():
    text = reps_to(s, 1000)
    def med(f):
        xs = []
        for _ in range(20):
            t0 = time.perf_counter(); f(); xs.append(time.perf_counter() - t0)
        return 1e3 * sorted(xs)[10]
    c = canonicalize(text, 4096)
    print(f"{name:<34} {len(text):>6} {med(lambda: canonicalize(text, 4096)):>12.3f} {med(lambda: m.scan(c.text)):>9.3f} "
          f"{med(lambda: tok.encode(c.text, add_special_tokens=False)):>9.3f}")
