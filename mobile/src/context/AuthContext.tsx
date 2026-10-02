import React,{createContext,useContext,useEffect,useMemo,useState} from "react";
import {clearTokens,getMe,hasStoredSession,login as apiLogin,logout as apiLogout,register as apiRegister} from "../api";
import type {User} from "../types";
type Ctx={user:User|null;loading:boolean;signIn:(id:string,pw:string)=>Promise<void>;signUp:(x:{name:string;username:string;gmail:string;phone:string;password:string})=>Promise<void>;signOut:()=>Promise<void>;updateUser:(u:User)=>void};
const Context=createContext<Ctx|null>(null);
export function AuthProvider({children}:{children:React.ReactNode}){const[user,setUser]=useState<User|null>(null);const[loading,setLoading]=useState(true);useEffect(()=>{(async()=>{try{if(await hasStoredSession())setUser((await getMe()).user);}catch{await clearTokens();}finally{setLoading(false);}})();},[]);const value=useMemo<Ctx>(()=>({user,loading,signIn:async(id,pw)=>setUser(await apiLogin(id,pw)),signUp:async(x)=>setUser(await apiRegister(x)),signOut:async()=>{await apiLogout();setUser(null);},updateUser:setUser}),[user,loading]);return <Context.Provider value={value}>{children}</Context.Provider>;}
export function useAuth(){const v=useContext(Context);if(!v)throw new Error("useAuth must be used inside AuthProvider");return v;}
