User Guide
==========

This guide is for end users who want to try the current supported workflows on
the ``main`` branch without reading through the internal runtime details.

The guide is intentionally practical. It focuses on:

- starting the mock BehavBox safely
- running the current reference task
- finding the output files afterward
- fixing the most common problems

It does **not** try to explain the internal service architecture. If you need
that, use the reference pages linked from the main index.

What This Guide Covers Today
----------------------------

The current supported tutorial workflow is:

1. launch the mock BehavBox user interface (UI)
2. run the reference ``head_fixed_gonogo`` sample task
3. trigger responses manually or with the fake mouse
4. inspect the output files written at the end of the run

This matches what the current hardware repo actually supports today. It does
not try to document future browser workflows or additional task templates that
are still in planning or early development.

.. toctree::
   :maxdepth: 1

   user_guide_first_run
   user_guide_sample_task
   user_guide_outputs
   user_guide_troubleshooting
