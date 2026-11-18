"""Nonblocking audition of one cue or a short sample of a sound set."""

from collections.abc import Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event, Lock
from time import monotonic

from miniaudio import Backend

from music_player import PreviewPlayer

from .audio import Clip
from .catalogue import EVENT_BY_STEM, event_for
from .sets import SoundSet

_SAMPLER_ORDER = ("draw", "summon", "activate", "attack", "damage", "phase")
_SAMPLER_CUE_SECONDS = 2
_SAMPLER_MAX_CUES = 6
_SAMPLER_GAP = 0.12


class AuditionPlayer:
    """Playback and device cleanup run on one worker. A new preview replaces the old one."""

    def __init__(self, *, backends: Iterable[Backend] | None = None) -> None:
        self._backends = list(backends) if backends is not None else None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sound-audition")
        self._lock = Lock()
        self._cancel = Event()
        self._future: Future[None] | None = None
        self._closed = False

    @property
    def playing(self) -> bool:
        with self._lock:
            return (
                self._future is not None and not self._future.done() and not self._cancel.is_set()
            )

    def _submit(self, clips: tuple[Clip, ...], *, sampler: bool) -> Future[None]:
        with self._lock:
            if self._closed:
                raise RuntimeError("This audition player is closed.")
            self._stop()
            self._cancel = Event()
            self._future = self._executor.submit(self._play, clips, self._cancel, sampler)
            return self._future

    def preview(self, sound_set: SoundSet, key: str) -> Future[None]:
        """An explicit silence stops playback. An unassigned event has no preview."""
        event = event_for(key)
        if key not in sound_set.assignments:
            raise ValueError(f"{sound_set.name} has no choice for {event.name}.")
        clip = sound_set.assignments[key]
        return self._submit((clip,) if clip is not None else (), sampler=False)

    def sampler(self, sound_set: SoundSet, *, events: Iterable[str] | None = None) -> Future[None]:
        """Audition up to six assigned clips; use at most two seconds from each clip."""
        order = (
            (*_SAMPLER_ORDER, *EVENT_BY_STEM, *sound_set.assignments) if events is None else events
        )
        clips = []
        for key in dict.fromkeys(order):
            event_for(key)
            if (clip := sound_set.assignments.get(key)) is not None:
                clips.append(clip)
                if len(clips) == _SAMPLER_MAX_CUES:
                    break
        return self._submit(tuple(clips), sampler=True)

    def _play(self, clips: tuple[Clip, ...], cancel: Event, sampler: bool) -> None:
        player = PreviewPlayer(backends=self._backends)
        for index, clip in enumerate(clips):
            if cancel.is_set():
                return
            try:
                player.play_memory(clip.data, duration=clip.duration)
                cutoff = monotonic() + _SAMPLER_CUE_SECONDS if sampler else float("inf")
                while player.playing and (remaining := cutoff - monotonic()) > 0:
                    if cancel.wait(min(0.02, remaining)):
                        return
            finally:
                player.stop()
            if index + 1 < len(clips) and cancel.wait(_SAMPLER_GAP):
                return

    def _stop(self) -> None:
        # A queued job checks cancellation before opening the device. Leaving
        # the Future completable also avoids running its callbacks inside our lock.
        self._cancel.set()

    def stop(self) -> None:
        with self._lock:
            self._stop()

    def close(self) -> None:
        with self._lock:
            self._stop()
            self._closed = True
        self._executor.shutdown(wait=False)
