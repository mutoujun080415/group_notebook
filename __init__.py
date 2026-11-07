"""群聊摘录（Group Notebook）插件入口包。

插件加载器会把入口文件以 ``group_notebook.plugin`` 的形式直接执行，
此时父包尚未初始化完成；急切的 ``from .plugin import ...`` 会造成循环导入。
因此这里用模块级 ``__getattr__`` 按需导出插件类。
"""

from typing import Any

__all__ = ["GroupNotebookPlugin"]


def __getattr__(name: str) -> Any:
    """按需导入插件类，避免插件加载过程中的循环导入。"""
    if name == "GroupNotebookPlugin":
        from .plugin import GroupNotebookPlugin

        return GroupNotebookPlugin
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
