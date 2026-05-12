from django import forms

from .models import JobPreferences


SOURCE_CHOICES = [
    ("linkedin", "LinkedIn"),
    ("indeed", "Indeed"),
    ("glassdoor", "Glassdoor"),
]

EXPERIENCE_CHOICES = [
    ("internship", "Internship"),
    ("entry", "Entry level"),
    ("associate", "Associate"),
    ("mid_senior", "Mid-Senior"),
    ("director", "Director"),
    ("executive", "Executive"),
]

WORKPLACE_CHOICES = [
    ("onsite", "On-site"),
    ("remote", "Remote"),
    ("hybrid", "Hybrid"),
]


class JobPreferencesForm(forms.ModelForm):
    """Edit form for JobPreferences. The list-valued fields are stored as JSON
    on the model but presented to the user as comma-separated strings (locations)
    or multiple-choice checkboxes (sources / experience_levels / workplace_types)."""

    locations_text = forms.CharField(
        max_length=500,
        required=True,
        label="Locations",
        help_text="Comma-separated. e.g. 'Remote, Berlin, Cairo'",
        widget=forms.TextInput(attrs={
            "placeholder": "Remote, Berlin, Cairo",
            "class": "w-full rounded-rn px-4 py-3 bg-white dark:bg-neutral-900 ring-1 ring-neutral-200 dark:ring-neutral-800",
        }),
    )
    sources = forms.MultipleChoiceField(
        choices=SOURCE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=True,
    )
    experience_levels = forms.MultipleChoiceField(
        choices=EXPERIENCE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text="LinkedIn only.",
    )
    workplace_types = forms.MultipleChoiceField(
        choices=WORKPLACE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text="LinkedIn only.",
    )

    class Meta:
        model = JobPreferences
        fields = [
            "keyword",
            "date_posted",
            "max_jobs",
        ]
        widgets = {
            "keyword": forms.TextInput(attrs={
                "placeholder": "e.g. Backend Engineer",
                "class": "w-full rounded-rn px-4 py-3 bg-white dark:bg-neutral-900 ring-1 ring-neutral-200 dark:ring-neutral-800",
            }),
            "date_posted": forms.RadioSelect,
            "max_jobs": forms.NumberInput(attrs={
                "min": 1, "max": 200,
                "class": "w-32 rounded-rn px-4 py-3 bg-white dark:bg-neutral-900 ring-1 ring-neutral-200 dark:ring-neutral-800",
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance: JobPreferences | None = kwargs.get("instance")
        if instance is not None:
            self.fields["locations_text"].initial = ", ".join(instance.locations or [])
            self.fields["sources"].initial = list(instance.sources or [])
            self.fields["experience_levels"].initial = list(instance.experience_levels or [])
            self.fields["workplace_types"].initial = list(instance.workplace_types or [])
        # Sensible default if first edit
        if not self.fields["sources"].initial:
            self.fields["sources"].initial = ["linkedin"]

    def clean_locations_text(self):
        raw = self.cleaned_data.get("locations_text", "")
        items = [x.strip() for x in raw.split(",") if x.strip()]
        if not items:
            raise forms.ValidationError("Provide at least one location (use 'Remote' if you don't care).")
        return items

    def clean_max_jobs(self):
        n = self.cleaned_data.get("max_jobs", 30)
        if n < 1 or n > 200:
            raise forms.ValidationError("Must be between 1 and 200.")
        return n

    def save(self, commit: bool = True):
        instance: JobPreferences = super().save(commit=False)
        instance.locations = self.cleaned_data["locations_text"]
        instance.sources = list(self.cleaned_data["sources"])
        instance.experience_levels = list(self.cleaned_data.get("experience_levels") or [])
        instance.workplace_types = list(self.cleaned_data.get("workplace_types") or [])
        if commit:
            instance.save()
        return instance


def seed_defaults_from_profile(prefs: JobPreferences, profile) -> None:
    """Auto-seed empty preferences from the user's parsed CV.

    - keyword <- most recent experience title
    - locations <- profile.location (single-element list) or ['Remote']
    - sources <- ['linkedin']
    - workplace_types <- ['remote'] if profile.location is blank, else []
    """
    data = (profile.data_content or {}) if profile else {}

    if not prefs.keyword:
        title = ""
        experiences = data.get("experiences") or []
        if experiences and isinstance(experiences, list):
            first = experiences[0] or {}
            title = first.get("title") or first.get("role") or ""
        prefs.keyword = (title or "").strip()

    if not prefs.locations:
        loc = (getattr(profile, "location", None) or "").strip()
        prefs.locations = [loc] if loc else ["Remote"]

    if not prefs.sources:
        prefs.sources = ["linkedin"]

    if not prefs.workplace_types and not (getattr(profile, "location", None) or "").strip():
        prefs.workplace_types = ["remote"]
