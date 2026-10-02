import * as SecureStore from "expo-secure-store";
import { API_V1 } from "./config";
import type {Conversation,Message,Post,SearchSpace,SearchUser,Story,User,Workspace} from "./types";
const ACCESS_KEY="uc_access_token"; const REFRESH_KEY="uc_refresh_token";
type Tokens={access_token:string;refresh_token:string;expires_in?:number;refresh_expires_in?:number};
export class ApiError extends Error{status:number;constructor(message:string,status:number){super(message);this.status=status;}}
async function saveTokens(tokens:Tokens){await SecureStore.setItemAsync(ACCESS_KEY,tokens.access_token);await SecureStore.setItemAsync(REFRESH_KEY,tokens.refresh_token);}
export async function clearTokens(){await SecureStore.deleteItemAsync(ACCESS_KEY);await SecureStore.deleteItemAsync(REFRESH_KEY);}
export async function hasStoredSession(){return Boolean(await SecureStore.getItemAsync(REFRESH_KEY));}
async function refreshTokens(){const refresh=await SecureStore.getItemAsync(REFRESH_KEY);if(!refresh)return false;try{const r=await fetch(API_V1+"/auth/refresh",{method:"POST",headers:{"Content-Type":"application/json","Accept":"application/json"},body:JSON.stringify({refresh_token:refresh})});if(!r.ok){await clearTokens();return false;}await saveTokens(await r.json());return true;}catch{return false;}}
async function parse<T>(response:Response):Promise<T>{const text=await response.text();let p:any={};try{p=text?JSON.parse(text):{};}catch{}if(!response.ok)throw new ApiError(p.error||("Request failed ("+response.status+")"),response.status);return p as T;}
export async function apiRequest<T>(path:string,init:RequestInit={},retry=true):Promise<T>{const access=await SecureStore.getItemAsync(ACCESS_KEY);const headers=new Headers(init.headers);headers.set("Accept","application/json");if(access)headers.set("Authorization","Bearer "+access);const r=await fetch(API_V1+path,{...init,headers});if(r.status===401&&retry&&!path.startsWith("/auth/")){if(await refreshTokens())return apiRequest<T>(path,init,false);}return parse<T>(r);}
export async function jsonRequest<T>(path:string,method:string,body?:unknown){return apiRequest<T>(path,{method,headers:{"Content-Type":"application/json"},body:body===undefined?undefined:JSON.stringify(body)});}
export async function login(identifier:string,password:string){const r=await jsonRequest<{access_token:string;refresh_token:string;user:User}>("/auth/login","POST",{identifier,password});await saveTokens(r);return r.user;}
export async function register(input:{name:string;username:string;gmail:string;phone:string;password:string}){const r=await jsonRequest<{access_token:string;refresh_token:string;user:User}>("/auth/register","POST",{...input,confirm_password:input.password});await saveTokens(r);return r.user;}
export async function logout(){try{const refresh=await SecureStore.getItemAsync(REFRESH_KEY);await jsonRequest("/auth/logout","POST",{refresh_token:refresh});}catch{}finally{await clearTokens();}}
export const getMe=()=>apiRequest<{user:User}>("/me");
export const updateMe=(body:Partial<User>)=>jsonRequest<{user:User}>("/me","PATCH",body);
export const getFeed=(offset=0)=>apiRequest<{posts:Post[];has_more:boolean;next_offset:number}>("/feed?offset="+offset+"&limit=12");
export const getReels=(offset=0)=>apiRequest<{posts:Post[];has_more:boolean;next_offset:number}>("/reels?offset="+offset+"&limit=12");
export const getStories=()=>apiRequest<{stories:Story[]}>("/stories");
export function makeMediaPart(asset:{uri:string;fileName?:string|null;mimeType?:string|null;type?:string|null}){const video=asset.type==="video"||Boolean(asset.mimeType&&asset.mimeType.startsWith("video/"));return{uri:asset.uri,name:asset.fileName||("upload-"+Date.now()+(video?".mp4":".jpg")),type:asset.mimeType||(video?"video/mp4":"image/jpeg")};}
export async function createPost(mode:"post"|"reel",caption:string,asset?:{uri:string;fileName?:string|null;mimeType?:string|null;type?:string|null}){const form=new FormData();form.append("post_type",mode);form.append("caption",caption);if(asset)form.append("media",makeMediaPart(asset) as any);return apiRequest<{ok:boolean;post_id:number;post_type:string;media_url?:string|null}>("/posts",{method:"POST",body:form});}
export async function createStory(caption:string,asset:{uri:string;fileName?:string|null;mimeType?:string|null;type?:string|null}){const form=new FormData();form.append("caption",caption);form.append("media",makeMediaPart(asset) as any);return apiRequest<{ok:boolean;story_id:number;media_url:string}>("/stories",{method:"POST",body:form});}
export const likePost=(id:number)=>apiRequest<{liked:boolean;like_count:number}>("/posts/"+id+"/like",{method:"POST"});
export const commentPost=(id:number,comment:string)=>jsonRequest<{ok:boolean;comment_count:number}>("/posts/"+id+"/comments","POST",{comment});
export const getConversations=()=>apiRequest<{conversations:Conversation[]}>("/conversations");
export const getMessages=(id:number,since=0)=>apiRequest<{messages:Message[];blocked:boolean}>("/messages/"+id+"?since_id="+since);
export const sendMessage=(id:number,message:string)=>jsonRequest<{ok:boolean;message:Message}>("/messages/"+id,"POST",{message});
export const getNotifications=()=>apiRequest<{notifications:Array<Record<string,unknown>}>("/notifications");
export const search=(q:string)=>apiRequest<{users:SearchUser[];spaces:SearchSpace[]}>("/search?q="+encodeURIComponent(q));
export const getWorkspace=()=>apiRequest<Workspace>("/workspace");
