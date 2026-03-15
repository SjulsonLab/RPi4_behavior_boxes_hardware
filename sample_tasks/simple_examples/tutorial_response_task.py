"""Tracked example task file for the simple task API tutorial."""

from sample_tasks.simple_api import SimpleTask


task = SimpleTask(name="tutorial_response", box_profile="head_fixed")
task.param("reward_amount_ul", 25.0)
task.cue("go", duration_s=0.2, side="both")
task.state("cue").on_enter(task.play_cue("go")).after(0.2, goto="response_window")
task.state("response_window").on_event(
    "center_entry",
    task.record_event("response_detected"),
    task.deliver_reward(output_name="reward_center", amount_ul="reward_amount_ul"),
    goto="rewarded",
).after(5.0, goto="timed_out")
task.state("rewarded").finish("response_received")
task.state("timed_out").finish("response_window_elapsed")

TASK = task.build()
