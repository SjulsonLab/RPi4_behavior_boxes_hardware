import threading

try:
    from essential.mock_hw.registry import set_visual_stim_state
except ImportError:
    from mock_hw.registry import set_visual_stim_state


class MockVisualStim:
    def __init__(self, session_info):
        self.session_info = session_info
        self._timer = None
        self._default_duration = float(session_info.get("mock_visual_stim_duration_s", 1.0))
        set_visual_stim_state(
            visual_stim_enabled=True,
            visual_stim_active=False,
            current_grating=None,
        )

    def load_grating_file(self, grating_file):
        return None

    def load_grating_dir(self, grating_directory):
        return None

    def load_session_gratings(self):
        return None

    def list_gratings(self):
        return None

    def clear_gratings(self):
        return None

    def show_grating(self, grating_name):
        if self._timer is not None:
            self._timer.cancel()

        set_visual_stim_state(
            visual_stim_enabled=True,
            visual_stim_active=True,
            current_grating=grating_name,
        )

        self._timer = threading.Timer(self._default_duration, self._end_grating)
        self._timer.daemon = True
        self._timer.start()

    def process_function(self, grating_name):
        # Compatibility shim with real VisualStim API.
        self.show_grating(grating_name)

    def _end_grating(self):
        set_visual_stim_state(
            visual_stim_enabled=True,
            visual_stim_active=False,
            current_grating=None,
        )

    def __del__(self):
        if self._timer is not None:
            self._timer.cancel()
        self._end_grating()
