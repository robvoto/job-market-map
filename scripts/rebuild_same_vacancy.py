import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collector.duplicates import repair_conflicting_same_vacancy_groups

if __name__ == "__main__":
    groups, members, links = repair_conflicting_same_vacancy_groups()
    print(f"repaired_groups={groups} repaired_members={members} same_vacancy_links={links}")
