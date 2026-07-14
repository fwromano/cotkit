"""TCP stream framing for CoT.

TAK CoT streams are a sequence of ``<event>...</event>`` XML documents
over a raw TCP (or TLS) byte stream — there is no length prefix and no
guaranteed delimiter (some peers append newlines, some don't), and a
single ``recv()`` can end mid-tag or mid-multibyte-character.
``CotStreamParser`` buffers incoming data and yields only complete
event documents.

Robustness properties (each covered by a test):
- an ``<event`` open tag split across reads is reassembled;
- a UTF-8 character split across reads is decoded correctly
  (incremental decoder — never two replacement chars);
- an unterminated/garbage ``<event`` fragment cannot swallow the next
  valid event (the fragment is dropped, counted in
  ``dropped_fragments``);
- a peer streaming an endless unterminated event cannot grow memory
  beyond ``max_buffer`` (drops counted in ``dropped_oversize``,
  approximate when the garbage itself contains ``<event``).
"""

from __future__ import annotations

import codecs

__all__ = ["CotStreamParser", "DEFAULT_MAX_BUFFER"]

DEFAULT_MAX_BUFFER = 4 * 1024 * 1024  # 4 MiB of pending, unterminated event


class CotStreamParser:
    """Buffer raw stream data; extract complete ``<event>...</event>`` docs.

    Noise between events (newlines, XML declarations, garbage) is
    discarded. Feed it ``str`` or ``bytes`` (bytes go through an
    incremental UTF-8 decoder with ``errors="replace"``); it returns a
    list of complete event XML strings per call.
    """

    _EVENT_START = "<event"
    _EVENT_END = "</event>"

    def __init__(self, max_buffer: int = DEFAULT_MAX_BUFFER):
        self.buffer = ""
        self.max_buffer = max_buffer
        self.dropped_oversize = 0   # events discarded for exceeding max_buffer
        self.dropped_fragments = 0  # unterminated fragments displaced by a later event
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def feed(self, data) -> list:
        """Feed stream data, return list of complete event XML strings."""
        if isinstance(data, (bytes, bytearray)):
            data = self._decoder.decode(bytes(data))
        self.buffer += data

        events = []
        while True:
            start = self.buffer.find(self._EVENT_START)
            if start == -1:
                # No event begins here. Keep only a possible split prefix
                # of "<event" (e.g. chunk ends in "<ev"); drop the rest.
                keep = ""
                max_prefix = len(self._EVENT_START) - 1
                tail = self.buffer[-max_prefix:] if max_prefix > 0 else self.buffer
                for i in range(max_prefix, 0, -1):
                    prefix = self._EVENT_START[:i]
                    if tail.endswith(prefix):
                        keep = prefix
                        break
                self.buffer = keep
                break

            end = self.buffer.find(self._EVENT_END, start)
            if end == -1:
                # Event started but not yet terminated: keep it pending —
                # unless it has outgrown the cap, in which case drop it
                # and rescan whatever followed.
                pending = self.buffer[start:]
                if len(pending) > self.max_buffer:
                    self.dropped_oversize += 1
                    self.buffer = self.buffer[start + len(self._EVENT_START):]
                    continue
                self.buffer = pending
                break

            end += len(self._EVENT_END)
            segment = self.buffer[start:end]
            self.buffer = self.buffer[end:]

            # If another "<event" occurs inside the segment, the leading
            # part is an unterminated fragment whose missing end tag was
            # supplied by a LATER event — emitting the blob would destroy
            # that valid event. Valid CoT never nests <event> and raw '<'
            # is illegal in XML attributes, so the last "<event" marks
            # the real document.
            inner = segment.rfind(self._EVENT_START)
            if inner > 0:
                self.dropped_fragments += 1
                segment = segment[inner:]
            events.append(segment)
        return events
