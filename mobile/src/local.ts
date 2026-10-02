import * as SecureStore from "expo-secure-store";

const DRAFT_KEY="uc_create_draft_v1";
const RESUME_KEY="uc_resume_route_v1";
const NOTIFICATION_MUTE_KEY="uc_notification_mutes_v1";

export type CreateDraft={mode:"post"|"reel"|"story";caption:string;asset?:{uri:string;fileName?:string|null;mimeType?:string|null;type?:string|null}|null};

export async function saveCreateDraft(draft:CreateDraft){await SecureStore.setItemAsync(DRAFT_KEY,JSON.stringify(draft));}
export async function getCreateDraft():Promise<CreateDraft|null>{try{const raw=await SecureStore.getItemAsync(DRAFT_KEY);return raw?JSON.parse(raw):null;}catch{return null;}}
export async function clearCreateDraft(){await SecureStore.deleteItemAsync(DRAFT_KEY);}

export async function rememberResumeRoute(route:string){if(route)await SecureStore.setItemAsync(RESUME_KEY,route);}
export async function getResumeRoute(){return SecureStore.getItemAsync(RESUME_KEY);}

export async function getMutedNotificationCategories():Promise<string[]>{try{const raw=await SecureStore.getItemAsync(NOTIFICATION_MUTE_KEY);const value=raw?JSON.parse(raw):[];return Array.isArray(value)?value.map(String):[];}catch{return [];}}
export async function setMutedNotificationCategories(categories:string[]){await SecureStore.setItemAsync(NOTIFICATION_MUTE_KEY,JSON.stringify([...new Set(categories)]));}
