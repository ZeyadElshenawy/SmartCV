import re

with open(r'templates/profiles/manual_form.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Helper function addition
helper = """            hasValue(val) {
                if (val === null || val === undefined) return false;
                if (typeof val === 'string' && val.trim() === '') return false;
                if (Array.isArray(val) && val.length === 0) return false;
                if (typeof val === 'object' && Object.keys(val).length === 0) return false;
                return true;
            },
            init() {"""
content = content.replace('            init() {', helper)

# Experience
content = re.sub(
    r'(<div>\s*<label[^>]*>Job Title</label>\s*<input type="text" x-model="exp\.title")',
    r'<div x-show="hasValue(exp.title)">\n                            <label class="block text-xs font-bold text-gray-500 dark:text-gray-400 uppercase mb-1">Job Title</label>\n                            <input type="text" x-model="exp.title"',
    content
)
content = re.sub(
    r'(<div>\s*<label[^>]*>Company</label>\s*<input type="text" x-model="exp\.company")',
    r'<div x-show="hasValue(exp.company)">\n                            <label class="block text-xs font-bold text-gray-500 dark:text-gray-400 uppercase mb-1">Company</label>\n                            <input type="text" x-model="exp.company"',
    content
)
content = re.sub(
    r'(<div>\s*<label[^>]*>Duration</label>\s*<input type="text" x-model="exp\.duration")',
    r'<div x-show="hasValue(exp.duration)">\n                            <label class="block text-xs font-bold text-gray-500 dark:text-gray-400 uppercase mb-1">Duration</label>\n                            <input type="text" x-model="exp.duration"',
    content
)
content = re.sub(
    r'(<div class="mb-4">\s*<label[^>]*>Description</label>\s*<textarea x-model="exp\.description")',
    r'<div class="mb-4" x-show="hasValue(exp.description)">\n                        <label class="block text-xs font-bold text-gray-500 dark:text-gray-400 uppercase mb-1">Description</label>\n                        <textarea x-model="exp.description"',
    content
)
content = re.sub(
    r'(<div>\s*<div class="flex justify-between items-end mb-2">\s*<label[^>]*>Highlights \(Bullet Points\)</label>)',
    r'<div x-show="hasValue(exp.highlights)">\n                        <div class="flex justify-between items-end mb-2">\n                            <label class="block text-xs font-bold text-gray-500 dark:text-gray-400 uppercase">Highlights (Bullet Points)</label>',
    content
)

# Education
content = re.sub(
    r'(<div>\s*<label[^>]*>Degree</label>\s*<input type="text" x-model="edu\.degree")',
    r'<div x-show="hasValue(edu.degree)">\n                            <label class="block text-xs font-bold text-gray-500 dark:text-gray-400 uppercase mb-1">Degree</label>\n                            <input type="text" x-model="edu.degree"',
    content
)
content = re.sub(
    r'(<div>\s*<label[^>]*>Institution</label>\s*<input type="text" x-model="edu\.institution")',
    r'<div x-show="hasValue(edu.institution)">\n                            <label class="block text-xs font-bold text-gray-500 dark:text-gray-400 uppercase mb-1">Institution</label>\n                            <input type="text" x-model="edu.institution"',
    content
)
content = re.sub(
    r'(<div>\s*<label[^>]*>Year</label>\s*<input type="text" x-model="edu\.year")',
    r'<div x-show="hasValue(edu.year)">\n                            <label class="block text-xs font-bold text-gray-500 dark:text-gray-400 uppercase mb-1">Year</label>\n                            <input type="text" x-model="edu.year"',
    content
)

# Projects
content = re.sub(
    r'(<div class="mb-3">\s*<label[^>]*>Project Name</label>\s*<input type="text" x-model="proj\.name")',
    r'<div class="mb-3" x-show="hasValue(proj.name)">\n                        <label class="block text-xs font-bold text-gray-500 dark:text-gray-400 uppercase mb-1">Project Name</label>\n                        <input type="text" x-model="proj.name"',
    content
)
content = re.sub(
    r'(<div class="mb-4">\s*<label[^>]*>Description</label>\s*<textarea x-model="proj\.description")',
    r'<div class="mb-4" x-show="hasValue(proj.description)">\n                        <label class="block text-xs font-bold text-gray-500 dark:text-gray-400 uppercase mb-1">Description</label>\n                        <textarea x-model="proj.description"',
    content
)
content = re.sub(
    r'(<div>\s*<div class="flex justify-between items-end mb-2">\s*<label[^>]*>Key Features \(Bullet Points\)</label>)',
    r'<div x-show="hasValue(proj.highlights)">\n                        <div class="flex justify-between items-end mb-2">\n                            <label class="block text-xs font-bold text-gray-500 dark:text-gray-400 uppercase">Key Features (Bullet Points)</label>',
    content
)

with open(r'templates/profiles/manual_form.html', 'w', encoding='utf-8') as f:
    f.write(content)
