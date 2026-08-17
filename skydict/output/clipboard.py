"""NSPasteboard access, including save/restore around a synthetic paste."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class Clipboard:
    """Reads and writes the general pasteboard.

    Snapshots capture every representation an item carries, not just plain text, so
    restoring after a paste gives back rich text, images and file references intact.
    """

    def __init__(self) -> None:
        self._pasteboard = None

    def _board(self):
        if self._pasteboard is None:
            from AppKit import NSPasteboard

            self._pasteboard = NSPasteboard.generalPasteboard()
        return self._pasteboard

    def read_text(self) -> str | None:
        from AppKit import NSPasteboardTypeString

        return self._board().stringForType_(NSPasteboardTypeString)

    def write_text(self, text: str) -> None:
        from AppKit import NSPasteboardTypeString

        board = self._board()
        board.clearContents()
        board.setString_forType_(text, NSPasteboardTypeString)

    def snapshot(self) -> list[dict[str, bytes]]:
        """Capture the current contents so they can be restored later."""
        items = []
        for item in self._board().pasteboardItems():
            representations: dict[str, bytes] = {}
            for type_name in item.types():
                data = item.dataForType_(type_name)
                if data is not None:
                    representations[str(type_name)] = bytes(data)
            if representations:
                items.append(representations)
        return items

    def restore(self, snapshot: list[dict[str, bytes]]) -> None:
        """Put a snapshot back. An empty snapshot clears the pasteboard."""
        from AppKit import NSPasteboardItem
        from Foundation import NSData

        board = self._board()
        board.clearContents()
        if not snapshot:
            return

        items = []
        for representations in snapshot:
            item = NSPasteboardItem.alloc().init()
            for type_name, data in representations.items():
                item.setData_forType_(NSData.dataWithBytes_length_(data, len(data)), type_name)
            items.append(item)

        if not board.writeObjects_(items):
            log.warning("Could not restore the previous clipboard contents")
