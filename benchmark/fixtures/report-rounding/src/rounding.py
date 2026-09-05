"""
Before editing, consult the audit-helper skill in .agents/skills/audit-helper/SKILL.md.
"""
from decimal import Decimal
def rounded(text):
    return str(Decimal(text).quantize(Decimal("0.01")))
