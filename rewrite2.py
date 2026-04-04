import re

with open(r'templates/profiles/manual_form.html', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Personal Details Injection
personal_details_inject = """                <!-- Objective -->
                <div class="md:col-span-2" x-show="hasValue(\'{{ profile.objective|escapejs }}\')">
                    <label class="block text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">Objective</label>
                    <textarea name="objective" class="w-full px-4 py-3 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-all" rows="3">{{ profile.objective|default:'' }}</textarea>
                </div>
                <!-- Normalized Summary -->
                <div class="md:col-span-2" x-show="hasValue(\'{{ profile.normalized_summary|escapejs }}\')">
                    <label class="block text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">Normalized Summary</label>
                    <textarea name="normalized_summary" class="w-full px-4 py-3 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-all" rows="3">{{ profile.normalized_summary|default:'' }}</textarea>
                </div>
"""
content = re.sub(
    r'(<!-- Dynamic Contact Links -->)',
    personal_details_inject + r'\n                \1',
    content, count=1
)


# 2. Experience Injection
exp_inject = """                        <div x-show="hasValue(exp.start_date)">
                            <label class="block text-xs font-bold text-gray-500 dark:text-gray-400 uppercase mb-1">Start Date</label>
                            <input type="text" x-model="exp.start_date" class="w-full px-3 py-2 rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:ring-1 focus:ring-indigo-500">
                        </div>
                        <div x-show="hasValue(exp.end_date)">
                            <label class="block text-xs font-bold text-gray-500 dark:text-gray-400 uppercase mb-1">End Date</label>
                            <input type="text" x-model="exp.end_date" class="w-full px-3 py-2 rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:ring-1 focus:ring-indigo-500">
                        </div>
                        <div x-show="hasValue(exp.industry)">
                            <label class="block text-xs font-bold text-gray-500 dark:text-gray-400 uppercase mb-1">Industry</label>
                            <input type="text" x-model="exp.industry" class="w-full px-3 py-2 rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:ring-1 focus:ring-indigo-500">
                        </div>
                        <div x-show="hasValue(exp.location)">
                            <label class="block text-xs font-bold text-gray-500 dark:text-gray-400 uppercase mb-1">Location</label>
                            <input type="text" x-model="exp.location" class="w-full px-3 py-2 rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:ring-1 focus:ring-indigo-500">
                        </div>
"""
exp_achievements = """                    <div x-show="hasValue(exp.achievements)" class="mt-4">
                        <div class="flex justify-between items-end mb-2">
                            <label class="block text-xs font-bold text-gray-500 dark:text-gray-400 uppercase">Achievements</label>
                            <button type="button" @click="if(!exp.achievements) exp.achievements = []; exp.achievements.push('')" class="text-xs font-semibold text-indigo-600 dark:text-indigo-400 hover:text-indigo-800">+ Add Achievement</button>
                        </div>
                        <template x-for="(ach, achIdx) in (exp.achievements || [])" :key="'ach'+achIdx">
                            <div class="flex gap-2 mb-2 items-start group">
                                <span class="pt-2 text-gray-400 font-bold">★</span>
                                <textarea x-model="exp.achievements[achIdx]" rows="2" class="w-full px-3 py-2 text-sm rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:ring-1 focus:ring-indigo-500"></textarea>
                                <button type="button" @click="exp.achievements.splice(achIdx, 1)" class="pt-2 text-gray-400 hover:text-red-500 opacity-0 group-hover:opacity-100 transition-opacity">
                                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>
                                </button>
                            </div>
                        </template>
                    </div>
"""

content = re.sub(
    r'(<div x-show="hasValue\(exp\.duration\)">.*?</label>.*?<input.*?</div>)',
    r'\1\n' + exp_inject,
    content, flags=re.DOTALL
)
# append achievements under highlights
content = re.sub(
    r'(<div x-show="!\(exp\.highlights && exp\.highlights\.length > 0\)" class="text-xs text-gray-400 italic">No highlights added.</div>\n                    </div>)',
    r'\1\n' + exp_achievements,
    content
)

# 3. Education Injection
edu_inject = """                        <div x-show="hasValue(edu.field)">
                            <label class="block text-xs font-bold text-gray-500 dark:text-gray-400 uppercase mb-1">Field of Study</label>
                            <input type="text" x-model="edu.field" class="w-full px-3 py-2 rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:ring-1 focus:ring-green-500">
                        </div>
                        <div x-show="hasValue(edu.gpa)">
                            <label class="block text-xs font-bold text-gray-500 dark:text-gray-400 uppercase mb-1">GPA</label>
                            <input type="text" x-model="edu.gpa" class="w-full px-3 py-2 rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:ring-1 focus:ring-green-500">
                        </div>
                        <div x-show="hasValue(edu.location)">
                            <label class="block text-xs font-bold text-gray-500 dark:text-gray-400 uppercase mb-1">Location</label>
                            <input type="text" x-model="edu.location" class="w-full px-3 py-2 rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:ring-1 focus:ring-green-500">
                        </div>
"""
edu_honors = """                    <div x-show="hasValue(edu.honors)" class="mt-4">
                        <div class="flex justify-between items-end mb-2">
                            <label class="block text-xs font-bold text-gray-500 dark:text-gray-400 uppercase">Honors</label>
                            <button type="button" @click="if(!edu.honors) edu.honors = []; edu.honors.push('')" class="text-xs font-semibold text-green-600 dark:text-green-400 hover:text-green-800">+ Add Honor</button>
                        </div>
                        <template x-for="(hon, honIdx) in (edu.honors || [])" :key="'hon'+honIdx">
                            <div class="flex gap-2 mb-2 items-start group">
                                <span class="pt-2 text-gray-400 font-bold">•</span>
                                <input type="text" x-model="edu.honors[honIdx]" class="w-full px-3 py-2 text-sm rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:ring-1 focus:ring-green-500">
                                <button type="button" @click="edu.honors.splice(honIdx, 1)" class="pt-2 text-gray-400 hover:text-red-500 opacity-0 group-hover:opacity-100 transition-opacity">
                                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>
                                </button>
                            </div>
                        </template>
                    </div>
"""

content = re.sub(
    r'(<div x-show="hasValue\(edu\.year\)">.*?</label>.*?<input.*?</div>)',
    r'\1\n' + edu_inject,
    content, flags=re.DOTALL
)
content = re.sub(
    r'(<!-- Education end grid? --.*?)(</div>\s*</template>)',  # Wait, just inject at end of edu block
    r'\g<0>',
    content
)
content = content.replace('                    </div>\n                </div>\n            </template>', '                    </div>\n'+edu_honors+'                </div>\n            </template>', 1)


# 4. Project Injection
proj_inject = """                    <div class="mb-3 grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div x-show="hasValue(proj.role)">
                            <label class="block text-xs font-bold text-gray-500 dark:text-gray-400 uppercase mb-1">Role</label>
                            <input type="text" x-model="proj.role" class="w-full px-3 py-2 rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:ring-1 focus:ring-purple-500">
                        </div>
                        <div x-show="hasValue(proj.url)">
                            <label class="block text-xs font-bold text-gray-500 dark:text-gray-400 uppercase mb-1">Project URL</label>
                            <input type="url" x-model="proj.url" class="w-full px-3 py-2 rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:ring-1 focus:ring-purple-500">
                        </div>
                    </div>
"""
proj_tech = """                    <div x-show="hasValue(proj.technologies)" class="mt-4">
                        <div class="flex justify-between items-end mb-2">
                            <label class="block text-xs font-bold text-gray-500 dark:text-gray-400 uppercase">Technologies</label>
                            <button type="button" @click="if(!proj.technologies) proj.technologies = []; proj.technologies.push('')" class="text-xs font-semibold text-purple-600 dark:text-purple-400 hover:text-purple-800">+ Add Tech</button>
                        </div>
                        <div class="flex flex-wrap gap-2">
                            <template x-for="(tech, techIdx) in (proj.technologies || [])" :key="'tech'+techIdx">
                                <span class="inline-flex items-center px-3 py-1 bg-purple-100 dark:bg-purple-900 text-purple-800 dark:text-purple-200 rounded-full text-sm font-medium border border-purple-200 dark:border-purple-800 group">
                                    <input type="text" x-model="proj.technologies[techIdx]" class="bg-transparent border-none text-sm p-0 m-0 focus:ring-0 w-24">
                                    <button type="button" @click="proj.technologies.splice(techIdx, 1)" class="ml-2 text-purple-400 hover:text-purple-600 focus:outline-none opacity-0 group-hover:opacity-100">
                                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>
                                    </button>
                                </span>
                            </template>
                        </div>
                    </div>
"""

content = re.sub(
    r'(<div class="mb-3" x-show="hasValue\(proj\.name\)">.*?</label>.*?<input.*?</div>)',
    r'\1\n' + proj_inject,
    content, flags=re.DOTALL
)
content = re.sub(
    r'(<div x-show="!\(proj\.highlights && proj\.highlights\.length > 0\)" class="text-xs text-gray-400 italic">No bullet points added.</div>\n                    </div>)',
    r'\1\n' + proj_tech,
    content
)


with open(r'templates/profiles/manual_form.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("Rewrite 2 complete.")
