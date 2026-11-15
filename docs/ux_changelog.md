# SmartCV UX Changelog

All UX improvements tracked step-by-step with dates, files changed, and verification status.

---

## Step 1: Restructure Gap Analysis CTAs Based on Match Score
**Date**: 2026-03-30  
**Status**: ✅ Complete  

**Files Modified**:
- `analysis/views.py` — Added `primary_action` context variable based on match score thresholds (>80%: generate_resume, 50–80%: chat_fill_gaps, <50%: learning_path)
- `templates/analysis/gap_analysis.html` — Replaced static equal-weight CTAs with score-aware primary CTA + collapsed "More Actions" section for secondary options (cover letter, outreach, salary negotiation)

**Testing**:
- `python manage.py check` → 0 issues
- Template logic verified: three score tiers render different primary CTAs; secondary actions collapse into "More Actions" dropdown

---

## Step 2: Add Job Extraction Confirmation Step
**Date**: 2026-03-30  
**Status**: ✅ Complete  

**Files Modified**:
- `jobs/views.py` — Changed `job_input_view` to redirect to `review_extracted_job` instead of `gap_analysis`. Added new `review_extracted_job` view with editable fields and skill re-extraction on description change.
- `jobs/urls.py` — Added `review/<uuid:job_id>/` URL pattern
- `templates/jobs/review_job.html` — [NEW] Confirmation page showing job title, company, description, extracted skills with edit capability

**Testing**:
- `python manage.py check` → 0 issues
- Flow: Job Input → Review Extracted Job (editable) → Confirm & Analyze → Gap Analysis

---

## Step 3: Clarify Chatbot Profile Scope (Master vs. Job-Specific)
**Date**: 2026-03-30  
**Status**: ✅ Complete  

**Files Modified**:
- `profiles/models.py` — Added `JobProfileSnapshot` model to store per-job profile data.
- `templates/profiles/chatbot.html` — Replaced auto-redirect with scope confirmation modal ('Keep for All Jobs' vs 'Save Only for This Application').
- `profiles/views.py` — Added `chatbot_scope_decision` endpoint; stored pre-chatbot profile snapshot in session.
- `profiles/urls.py` — Added `chatbot/scope/<uuid:job_id>/` URL.

**Testing**:
- Migrations created and applied successfully.
- User can explicitly decide whether chatbot improvements apply globally or only to the targeted job application.

---

## Step 4: Fix Post-Download Dead End
**Date**: 2026-03-30  
**Status**: ✅ Complete  

**Files Modified**:
- `templates/resumes/preview.html` — Added Alpine.js `x-data` state to root div. Added `@click` handler to the Download PDF button setting `showNextStepsModal = true` with a timeout. Added modal HTML with Next Steps (Update Status, Generate Cover Letter).

**Testing**:
- When Download PDF is clicked, user sees a "Resume Downloaded!" modal with clear next steps keeping them in the app loop.

---

## Step 5: Fix Empty Dashboard Onboarding
**Date**: 2026-03-30  
**Status**: ✅ Complete  

**Files Modified**:
- `profiles/views.py` — Added calculation for `profile_complete`, `has_jobs`, and `show_onboarding` in the dashboard view.
- `templates/profiles/dashboard.html` — Added dynamic onboarding banner that displays visual steps to complete the profile and start the first application if the user hits the dashboard empty.

**Testing**:
- Users with empty profiles see a clear onboarding banner guiding them to upload a CV or start a job application instead of an empty Kanban board.

---

## Step 6: Reconnect Learning Path to Correct Context
**Date**: 2026-03-30  
**Status**: ✅ Complete  

**Files Modified**:
- `analysis/views.py` — Updated `generate_learning_path_view` to accept an optional `job_id` and filter gaps specifically to that job if present.
- `analysis/urls.py` — Added `learning-path/<uuid:job_id>/` routing.
- `templates/analysis/gap_analysis.html` — Updated Learning Path links to pass `job_id=job.id`, connecting the general action to the specific application context.

**Testing**:
- Clicking "Build Learning Path" from a specific Job's gap analysis now generates a curriculum tightly tied to the missing skills for that exact role.

---

## Step 7: Add Save Feedback to Resume Editor
**Date**: 2026-04-03  
**Status**: ✅ Complete  

**Files Modified**:
- `resumes/views.py` — Updated POST success redirect in `resume_edit_view` so that it redirects to the same page with `?saved=true` instead of immediately redirecting to the preview page.
- `templates/resumes/edit.html` — Configured Alpine inline variable to show a success toast based on the URL query param, added last saved timestamp in the header, and split the final action row into separate 'Save Changes' and 'Preview & Download' buttons.

**Testing**:
- Saving changes in the resume editor stays on the same page.
- "Resume saved successfully!" toast is shown for 3 seconds.
- The user can proceed to preview by clicking the distinct bottom CTA.

---

## Step 8: Enrich Resume History
**Date**: 2026-04-03  
**Status**: ✅ Complete  

**Files Modified**:
- `templates/resumes/list.html` — Updated the resume card design to prominently display the associated job title and company rather than the generic resume name. Added clear indicators for match score, ATS score, and creation date. Included a direct "Re-generate" action link to quickly iterate on that specific job's application.

**Testing**:
- Browsed resume history; cards display contextually relevant information (Job + Company) and color-coded scores instead of opaque names.
- "Re-generate" button correctly points to the `generate_resume` view for that specific job.

---

## Step 9: Reframe Profile Dashboard as "Career Snapshot"
**Date**: 2026-04-03  
**Status**: ✅ Complete  

**Files Modified**:
- `profiles/views.py` — Updated `review_master_profile` to compute summary statistics: total years of experience, number of top skills, number of projects, and number of education entries.
- `templates/profiles/manual_form.html` — Conditionally replaced the standard header with a "Career Snapshot" card layout, pulling computed fields to give a highly visual summary of the master profile. Added a simple back button CTA next to the form elements.

**Testing**:
- Viewed Master Profile from the sidebar; the four visual cards appear correctly at the top.
- "Looks Good - Back to Dashboard" securely provides a way out.

---

## Step 10: Chrome extension auth status indicator
**Date**: 2026-04-03  
**Status**: ✅ Complete  

**Files Modified**:
- `extension/popup.html` — Added an authentication status element with conditional styling for green/red dots.
- `extension/popup.js` — Updated the `DOMContentLoaded` event listener to fetch `http://127.0.0.1:8000/profiles/api/current/`. Depending on the response, updates the UI to show either "Connected as [name]" in green, or "Login Required [Link]" in red.

**Testing**:
- When logged into SmartCV locally and the extension popup opens, the dot turns green and reads "Connected as [User]".
- When logged out, the dot turns red and shows a login link.

---
