import { apiClient } from './client';

export type DigitalEmployeeStatus = 'active' | 'disabled';

export type DigitalEmployeeToolBinding = {
  toolId: string;
  enabledToolNames: string[];
};

export type DigitalEmployeeBindingHealth = {
  missingSkillIds: string[];
  missingToolIds: string[];
  missingToolNames: Array<{ toolId: string; names: string[] }>;
};

export type DigitalEmployee = {
  id: string;
  name: string;
  description: string;
  avatarUrl: string;
  manifesto: string;
  skillIds: string[];
  toolBindings: DigitalEmployeeToolBinding[];
  bindingHealth: DigitalEmployeeBindingHealth;
  status: DigitalEmployeeStatus;
  isPublic: boolean;
  // Assignment subjects (availability = 公开 或 指派), filled from assignment rows.
  roleIds: string[];
  userIds: string[];
  createdBy: string;
  updatedBy: string;
  createdAt: string;
  updatedAt: string;
};

export type DigitalEmployeeDraft = {
  name: string;
  description: string;
  avatarUrl: string;
  manifesto: string;
  skillIds: string[];
  toolBindings: DigitalEmployeeToolBinding[];
  status: DigitalEmployeeStatus;
  isPublic: boolean;
};

export type DigitalEmployeeListQuery = {
  status?: DigitalEmployeeStatus;
  q?: string;
};

export async function listDigitalEmployees(query: DigitalEmployeeListQuery = {}): Promise<DigitalEmployee[]> {
  return (await apiClient.get('/api/digital-employees', { params: query })).data;
}

export async function getDigitalEmployee(id: string): Promise<DigitalEmployee> {
  return (await apiClient.get(`/api/digital-employees/${id}`)).data;
}

export async function createDigitalEmployee(payload: DigitalEmployeeDraft): Promise<DigitalEmployee> {
  return (await apiClient.post('/api/digital-employees', payload)).data;
}

export async function updateDigitalEmployee(id: string, payload: DigitalEmployeeDraft): Promise<DigitalEmployee> {
  return (await apiClient.put(`/api/digital-employees/${id}`, payload)).data;
}

// REPLACE-ALL semantics: the employee ends up assigned to exactly these subjects.
export type DigitalEmployeeAssignments = {
  roleIds: string[];
  userIds: string[];
};

export async function replaceDigitalEmployeeAssignments(
  id: string,
  payload: DigitalEmployeeAssignments,
): Promise<DigitalEmployeeAssignments> {
  return (await apiClient.put(`/api/digital-employees/${id}/assignments`, payload)).data;
}

export async function deleteDigitalEmployee(id: string): Promise<void> {
  await apiClient.delete(`/api/digital-employees/${id}`);
}
