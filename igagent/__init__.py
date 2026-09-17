"""igagent — autonomní agent pro správu Instagram profilu.

Vrstvy:
  igagent.instagram  — Instagram Graph API (publikace, insights, komentáře)
  igagent.media      — generování grafiky, zpracování fotek, střih Reels
  igagent.brain      — Claude (nápady, texty, scénáře, analýzy)
  igagent.analytics  — sběr metrik, skórování, učící smyčka
  igagent.store      — SQLite perzistence
  igagent.pipeline   — orchestrace: plán → výroba → publikace → měření → učení
"""

__version__ = "0.1.0"
