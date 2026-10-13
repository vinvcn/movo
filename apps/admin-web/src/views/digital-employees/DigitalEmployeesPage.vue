<script setup lang="ts">
import { t } from '@/composables/i18n';
import { computed, h, onMounted, ref, type VNode } from 'vue';
import { NTag, useDialog, useMessage, type DataTableColumns, type SelectOption } from 'naive-ui';
import PageIntro from '@/components/PageIntro.vue';
import { formatAdminDateTime } from '@/composables/adminTimezone';
import { fetchSkills, type SkillItem } from '@/api/skills';
import { fetchTools, type ExternalToolItem, type ToolType } from '@/api/tools';
import { listPositionRoles, type PositionRole } from '@/api/positionRoles';
import { fetchUsers, type DirectoryUserItem } from '@/api/directory';
import {
  createDigitalEmployee, deleteDigitalEmployee, listDigitalEmployees, replaceDigitalEmployeeAssignments,
  updateDigitalEmployee,
  type DigitalEmployee, type DigitalEmployeeBindingHealth, type DigitalEmployeeDraft,
  type DigitalEmployeeStatus, type DigitalEmployeeToolBinding,
} from '@/api/digitalEmployees';

const message = useMessage();
const dialog = useDialog();
const loading = ref(false);
const saving = ref(false);
const employees = ref<DigitalEmployee[]>([]);
const editorVisible = ref(false);
const editing = ref<DigitalEmployee | null>(null);
const keyword = ref('');
const statusFilter = ref<DigitalEmployeeStatus | null>(null);
const optionsLoading = ref(false);
const skills = ref<SkillItem[]>([]);
const tools = ref<ExternalToolItem[]>([]);
const roles = ref<PositionRole[]>([]);
const users = ref<DirectoryUserItem[]>([]);
// Assignment subjects (指派可用范围); saved through the assignments endpoint.
const roleIds = ref<string[]>([]);
const userIds = ref<string[]>([]);

const emptyDraft = (): DigitalEmployeeDraft => ({
  name: '', description: '', avatarUrl: '', manifesto: '',
  skillIds: [], toolBindings: [], status: 'active', isPublic: false,
});
const draft = ref<DigitalEmployeeDraft>(emptyDraft());

const filtered = computed(() => {
  const needle = keyword.value.trim().toLowerCase();
  return employees.value.filter((item) => {
    const statusOk = !statusFilter.value || item.status === statusFilter.value;
    const keywordOk = !needle
      || item.name.toLowerCase().includes(needle)
      || item.description.toLowerCase().includes(needle);
    return statusOk && keywordOk;
  });
});

const statusOptions = [
  { label: t('启用'), value: 'active' },
  { label: t('停用'), value: 'disabled' },
];

// Only enabled organization skills may be bound; the list endpoint already
// scopes to organization skills.
const skillOptions = computed<SelectOption[]>(() => skills.value
  .filter((skill) => skill.enabled)
  .map((skill) => ({ label: skill.name, value: skill.id })));

const toolsById = computed(() => new Map(tools.value.map((tool) => [tool.id, tool])));

// Only active position roles may be assigned; disabled roles never match at runtime.
const roleOptions = computed<SelectOption[]>(() => roles.value
  .filter((role) => role.status === 'active')
  .map((role) => ({ label: role.name, value: role.id })));

// Tenant end users from the directory admin API; the select filters locally.
const userOptions = computed<SelectOption[]>(() => users.value
  .map((user) => ({ label: user.name || user.loginName, value: user.id })));

// Only active organization tools may be bound; personal tools never appear in
// the admin tools list (the endpoint filters by organization scope).
const toolOptions = computed<SelectOption[]>(() => tools.value
  .filter((tool) => tool.status === 'active')
  .map((tool) => ({
    label: tool.name,
    value: tool.id,
    render: ({ node }: { node: VNode }) => h('div', { class: 'tool-option' }, [
      node,
      h(NTag, { size: 'small', bordered: false, type: tool.type === 'mcp' ? 'success' : 'info' }, {
        default: () => (tool.type === 'mcp' ? 'MCP' : 'HTTP'),
      }),
    ]),
  })));

function bindingToolType(binding: DigitalEmployeeToolBinding): ToolType | '' {
  return toolsById.value.get(binding.toolId)?.type || '';
}

function discoveredNameOptions(toolId: string): SelectOption[] {
  const tool = toolsById.value.get(toolId);
  if (!tool) return [];
  const seen = new Set<string>();
  const options: SelectOption[] = [];
  for (const item of tool.discoveredTools || []) {
    const name = String(item?.name || '').trim();
    if (name && !seen.has(name)) {
      seen.add(name);
      options.push({ label: name, value: name });
    }
  }
  return options;
}

// The health report computed by the admin API; only shown when something is stale.
const bindingHealth = computed<DigitalEmployeeBindingHealth | null>(() => {
  const health = editing.value?.bindingHealth;
  if (!health) return null;
  if (health.missingSkillIds.length || health.missingToolIds.length || health.missingToolNames.length) return health;
  return null;
});

async function load() {
  loading.value = true;
  try {
    employees.value = await listDigitalEmployees();
  } finally { loading.value = false; }
}

async function loadOptions() {
  optionsLoading.value = true;
  try {
    const [skillRows, toolRows, roleRows, userRows] = await Promise.all([
      fetchSkills(), fetchTools(), listPositionRoles(), fetchUsers({}),
    ]);
    skills.value = skillRows;
    tools.value = toolRows;
    roles.value = roleRows;
    users.value = userRows;
  } catch {
    message.error(t('技能与工具列表加载失败'));
  } finally { optionsLoading.value = false; }
}

function openCreate() {
  editing.value = null;
  draft.value = emptyDraft();
  roleIds.value = [];
  userIds.value = [];
  editorVisible.value = true;
  void loadOptions();
}

function openEdit(employee: DigitalEmployee) {
  editing.value = employee;
  draft.value = {
    name: employee.name,
    description: employee.description,
    avatarUrl: employee.avatarUrl,
    manifesto: employee.manifesto,
    skillIds: [...employee.skillIds],
    toolBindings: employee.toolBindings.map((binding) => ({ toolId: binding.toolId, enabledToolNames: [...binding.enabledToolNames] })),
    status: employee.status,
    isPublic: employee.isPublic,
  };
  roleIds.value = [...employee.roleIds];
  userIds.value = [...employee.userIds];
  editorVisible.value = true;
  void loadOptions();
}

function addBinding() { draft.value.toolBindings.push({ toolId: '', enabledToolNames: [] }); }

function removeBinding(index: number) { draft.value.toolBindings.splice(index, 1); }

function onBindingToolChange(binding: DigitalEmployeeToolBinding, value: string | number | null) {
  binding.toolId = value == null ? '' : String(value);
  // Sub-tool names belong to the previously selected server; start clean.
  binding.enabledToolNames = [];
}

async function save() {
  if (!draft.value.name.trim()) return message.warning(t('请输入数字员工名称'));
  saving.value = true;
  try {
    if (editing.value) {
      await updateDigitalEmployee(editing.value.id, draft.value);
      // REPLACE-ALL semantics: the employee ends up assigned to exactly the selected subjects.
      await replaceDigitalEmployeeAssignments(editing.value.id, { roleIds: roleIds.value, userIds: userIds.value });
    } else {
      const created = await createDigitalEmployee(draft.value);
      if (roleIds.value.length || userIds.value.length) {
        await replaceDigitalEmployeeAssignments(created.id, { roleIds: roleIds.value, userIds: userIds.value });
      }
    }
    editorVisible.value = false; message.success(t('数字员工已保存')); await load();
  } catch (error: any) { message.error(error?.response?.data?.detail || error?.message || t('保存失败')); }
  finally { saving.value = false; }
}

function removeEmployee(employee: DigitalEmployee) {
  dialog.warning({
    title: t('删除数字员工'),
    content: t('确认删除数字员工“{name}”吗？删除后不可恢复。', { name: employee.name }),
    positiveText: t('删除'),
    negativeText: t('取消'),
    async onPositiveClick() {
      try {
        await deleteDigitalEmployee(employee.id);
        message.success(t('已删除'));
        await load();
      } catch (error: any) {
        // A referenced employee answers 409 with a Chinese detail; surface it as-is.
        message.error(error?.response?.data?.detail || t('删除失败'));
      }
    },
  });
}

const columns: DataTableColumns<DigitalEmployee> = [
  { title: t('数字员工'), key: 'name', render: row => h('div', { class: 'employee-name' }, [h('strong', row.name), h('small', row.description || t('暂无说明'))]) },
  { title: t('状态'), key: 'status', width: 100, render: row => h(NTag, { type: row.status === 'active' ? 'success' : 'default', bordered: false }, { default: () => row.status === 'active' ? t('启用') : t('停用') }) },
  { title: t('可用范围'), key: 'isPublic', width: 110, render: row => h(NTag, { type: row.isPublic ? 'info' : 'default', bordered: false }, { default: () => row.isPublic ? t('公开') : t('指派') }) },
  { title: t('更新时间'), key: 'updatedAt', width: 170, render: row => formatAdminDateTime(row.updatedAt, '-') },
  { title: t('操作'), key: 'actions', width: 140, render: row => h('div', { class: 'action-row' }, [
    h('button', { class: 'action-link', onClick: () => openEdit(row) }, t('编辑')),
    h('button', { class: 'action-link danger', onClick: () => removeEmployee(row) }, t('删除')),
  ]) },
];

onMounted(load);
</script>

<template>
  <div class="page-stack digital-employee-page">
    <n-card class="digital-employee-card" :bordered="false" size="large">
      <div class="digital-employee-content">
        <div class="page-head"><PageIntro :title="t('数字员工')" :description="t('配置数字员工的名字、宣言、可用范围与状态，员工会话将按此身份运行。')" /><n-button type="primary" @click="openCreate">{{ t('新建数字员工') }}</n-button></div>
        <div class="filter-row">
          <n-input v-model:value="keyword" clearable :placeholder="t('搜索名称或描述')" class="keyword-input" />
          <n-select v-model:value="statusFilter" clearable :options="statusOptions" :placeholder="t('状态')" class="status-select" />
          <n-button secondary @click="load">{{ t('刷新') }}</n-button>
        </div>
        <div class="employee-table-card"><n-data-table :columns="columns" :data="filtered" :loading="loading" :bordered="false" /></div>
      </div>
    </n-card>
    <n-drawer v-model:show="editorVisible" :width="720" placement="right">
      <n-drawer-content :title="editing ? t('编辑 {name}', { name: editing.name }) : t('新建数字员工')" closable>
        <div v-if="bindingHealth" class="health-warning">
          <strong>{{ t('以下引用已失效，会话运行时将跳过：') }}</strong>
          <div v-if="bindingHealth.missingSkillIds.length">{{ t('技能引用已失效：') }}{{ bindingHealth.missingSkillIds.join('、') }}</div>
          <div v-if="bindingHealth.missingToolIds.length">{{ t('工具引用已失效：') }}{{ bindingHealth.missingToolIds.join('、') }}</div>
          <div v-for="entry in bindingHealth.missingToolNames" :key="entry.toolId">{{ t('MCP 子工具引用已失效（工具 {toolId}）：', { toolId: entry.toolId }) }}{{ entry.names.join('、') }}</div>
        </div>
        <n-form :model="draft" label-placement="top">
          <n-grid :cols="2" :x-gap="14">
            <n-grid-item><n-form-item :label="t('名字')" required><n-input v-model:value="draft.name" :maxlength="64" /></n-form-item></n-grid-item>
            <n-grid-item><n-form-item :label="t('状态')"><n-radio-group v-model:value="draft.status"><n-radio value="active">{{ t('启用') }}</n-radio><n-radio value="disabled">{{ t('停用') }}</n-radio></n-radio-group></n-form-item></n-grid-item>
          </n-grid>
          <n-form-item :label="t('描述')"><n-input v-model:value="draft.description" type="textarea" :rows="2" :maxlength="512" /></n-form-item>
          <n-form-item :label="t('头像 URL')"><n-input v-model:value="draft.avatarUrl" :maxlength="512" /></n-form-item>
          <n-form-item :label="t('宣言')">
            <div class="manifesto-field">
              <n-input v-model:value="draft.manifesto" type="textarea" :rows="8" :maxlength="8192" show-count />
              <div class="field-hint">{{ t('宣言会注入数字员工会话的系统提示词，最多 8192 字符。') }}</div>
            </div>
          </n-form-item>
          <n-form-item :label="t('绑定技能')">
            <div class="binding-field">
              <n-select v-model:value="draft.skillIds" multiple clearable filterable :options="skillOptions" :loading="optionsLoading" :placeholder="t('选择企业技能')" />
              <div class="field-hint">{{ t('仅显示企业内启用的技能；员工会话的技能集即为所选集合。') }}</div>
            </div>
          </n-form-item>
          <n-form-item :label="t('绑定工具 (MCP / HTTP)')">
            <div class="binding-list">
              <div v-for="(binding, index) in draft.toolBindings" :key="index" class="binding-row">
                <div class="binding-row-main">
                  <n-select
                    class="binding-tool-select"
                    :value="binding.toolId"
                    :options="toolOptions"
                    :loading="optionsLoading"
                    filterable
                    :placeholder="t('选择企业工具')"
                    @update:value="(value: string | number | null) => onBindingToolChange(binding, value)"
                  />
                  <n-button size="small" quaternary type="error" @click="removeBinding(index)">{{ t('移除') }}</n-button>
                </div>
                <div v-if="bindingToolType(binding) === 'mcp'" class="binding-row-names">
                  <n-select
                    v-model:value="binding.enabledToolNames"
                    multiple
                    filterable
                    :options="discoveredNameOptions(binding.toolId)"
                    :placeholder="t('选择子工具')"
                  />
                  <div class="field-hint">{{ discoveredNameOptions(binding.toolId).length ? t('未选择子工具 = 该工具全部启用项') : t('该工具还没有发现 MCP 子工具，请先在「工具与 MCP」页完成发现。') }}</div>
                </div>
              </div>
              <div v-if="draft.toolBindings.length === 0" class="field-hint">{{ t('不绑定工具即为纯人设数字员工，仅由宣言定义。') }}</div>
              <n-button size="small" dashed @click="addBinding">{{ t('添加工具绑定') }}</n-button>
            </div>
          </n-form-item>
          <n-form-item :label="t('是否公开')">
            <div class="public-field">
              <n-switch v-model:value="draft.isPublic" />
              <span class="field-hint">{{ t('开启后企业内全员可用；关闭后需要指派才能使用。') }}</span>
            </div>
          </n-form-item>
          <n-form-item :label="t('岗位角色')">
            <div class="binding-field">
              <n-select v-model:value="roleIds" multiple clearable filterable :options="roleOptions" :loading="optionsLoading" :placeholder="t('选择岗位角色')" />
              <div class="field-hint">{{ t('仅显示启用中的岗位角色；关闭公开后，被指派角色的成员可使用此数字员工。') }}</div>
            </div>
          </n-form-item>
          <n-form-item :label="t('用户')">
            <div class="binding-field">
              <n-select v-model:value="userIds" multiple clearable filterable :options="userOptions" :loading="optionsLoading" :placeholder="t('选择用户')" />
              <div class="field-hint">{{ t('关闭公开后，被指派的用户可使用此数字员工；保存后立即生效。') }}</div>
            </div>
          </n-form-item>
        </n-form>
        <template #footer><div class="drawer-footer"><n-space justify="end"><n-button @click="editorVisible = false">{{ t('取消') }}</n-button><n-button type="primary" :loading="saving" @click="save">{{ t('保存') }}</n-button></n-space></div></template>
      </n-drawer-content>
    </n-drawer>
  </div>
</template>

<style scoped>
.digital-employee-page { min-height: calc(100vh - 98px); }
.digital-employee-card { min-width: 0; border-radius: 8px; }
.digital-employee-content { display: grid; gap: 16px; min-width: 0; }
.page-head { display: flex; align-items: center; justify-content: space-between; gap: 20px; }
.filter-row { display: flex; align-items: center; gap: 10px; }
.keyword-input { max-width: 280px; }
.status-select { width: 140px; }
.employee-table-card { overflow: hidden; border: 1px solid #edf0f5; border-radius: 10px; }
.employee-name { display: grid; gap: 2px; }
.employee-name small { color: #667085; font-weight: 400; }
.manifesto-field { display: grid; gap: 6px; width: 100%; }
.public-field { display: flex; align-items: center; gap: 10px; }
.field-hint { color: #667085; font-size: 12px; }
.binding-field { display: grid; gap: 6px; width: 100%; }
.binding-list { display: grid; gap: 10px; width: 100%; justify-items: start; }
.binding-row { display: grid; gap: 8px; width: 100%; padding: 10px; border: 1px solid #edf0f5; border-radius: 8px; }
.binding-row-main { display: flex; align-items: center; gap: 8px; width: 100%; }
.binding-row-main .binding-tool-select { flex: 1; min-width: 0; }
.binding-row-names { display: grid; gap: 6px; width: 100%; }
.health-warning { display: grid; gap: 4px; margin-bottom: 16px; padding: 10px 12px; border: 1px solid #ffd5de; border-radius: 8px; background: #fff6f8; color: #d03050; font-size: 12px; }
.tool-option { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.drawer-footer { min-width: 670px; }
@media (max-width: 860px) { .page-head { align-items: flex-start; flex-direction: column; } }
</style>
