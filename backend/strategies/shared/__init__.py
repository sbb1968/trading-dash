"""
strategies/shared/
──────────────────
Neutrale, strategi-AGNOSTISKE hjælpemoduler som FLERE strategier deler.

Hård-regel (se memory/strategi-isolation): der må ALDRIG være afhængigheder mellem
strategier. Delt kode (TV-screener, indikator-matematik) bor derfor HER — ikke under
nogen enkelt strategis pakke. En strategi må importere fra strategies.shared og
strategies.base, men ALDRIG fra en søskende-strategis pakke (strategies.confluence2,
strategies.europa_reversion, ...).
"""
