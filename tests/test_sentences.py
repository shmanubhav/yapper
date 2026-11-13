"""lib/sentences.py: split a <speak> block into playable sentences."""
import os, sys
sys.dont_write_bytecode = True     # keep the clone free of __pycache__

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
from sentences import split_sentences

ok = True


def check(c, label):
    global ok
    print(f"  {'PASS' if c else 'FAIL'}  {label}")
    ok = ok and c


s = split_sentences("The cluster is healthy. Six pull requests are mergeable! Want me to merge them?")
check(s == ["The cluster is healthy.", "Six pull requests are mergeable!", "Want me to merge them?"], f"splits on . ! ? {s}")

s = split_sentences("[gasp] All six pull requests passed on the first try. [laugh] That never happens.")
check(s == ["[gasp] All six pull requests passed on the first try.", "[laugh] That never happens."],
      f"a tag stays with the sentence it opens {s}")

s = split_sentences("Done. The backup finished cleanly, and all three hosts are reachable.")
check(s == ["Done. The backup finished cleanly, and all three hosts are reachable."], f"tiny sentence merges forward {s}")

s = split_sentences("The backup failed again, I am afraid. [sigh]")
check(s == ["The backup failed again, I am afraid. [sigh]"], f"trailing tiny piece merges back {s}")

s = split_sentences("Yes. Done. Okay.")
check(s == ["Yes. Done. Okay."], f"all tiny -> one utterance {s}")

s = split_sentences("The build averaged 5.0 minutes over ten runs. The cache sync is still broken.")
check(s == ["The build averaged 5.0 minutes over ten runs.", "The cache sync is still broken."], f"decimals do not split {s}")

check(split_sentences("") == [], "empty -> []")
check(split_sentences("   ") == [], "whitespace -> []")
check(split_sentences("One sentence only.") == ["One sentence only."], "single sentence")

sys.exit(0 if ok else 1)
