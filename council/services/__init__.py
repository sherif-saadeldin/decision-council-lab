"""Application service layer for boundary-safe orchestration.

Services own use-case orchestration and persistence coordination. They should
not import Rich or render directly. CLI, chat, and future UI surfaces should
call these services and keep presentation concerns in renderers.
"""

