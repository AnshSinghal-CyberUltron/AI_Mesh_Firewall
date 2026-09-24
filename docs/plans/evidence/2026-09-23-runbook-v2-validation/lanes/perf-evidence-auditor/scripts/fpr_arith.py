"""Arithmetic check of rb.md:2714 (GW10) and FINAL §11.5 I2. Independence (OR over W windows) is the
assumption under test; the 1%/window figure is the vendor operating point quoted by the plans."""
p = 0.01
for W in (1, 2, 3, 4, 7):
    f = 1 - (1 - p) ** W
    print(f"W={W}: 1-(0.99)^{W} = {f*100:.4f}%  -> false blocks/s @1,000 RPS = {1000*f:5.1f}  @1,064 RPS = {1064*f:5.1f}")
print("Bounds WITHOUT the independence assumption (Frechet): max(p) <= P(any window FP) <= min(1, W*p)")
for W in (2, 7):
    print(f"  W={W}: between {p*100:.2f}% (perfectly correlated windows) and {min(1,W*p)*100:.2f}% (mutually exclusive FPs)")
