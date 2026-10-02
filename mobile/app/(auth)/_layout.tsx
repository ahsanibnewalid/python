import {Redirect,Stack} from "expo-router";import {useAuth} from "../../src/context/AuthContext";
export default function AuthLayout(){const{user,loading}=useAuth();if(!loading&&user)return <Redirect href="/(tabs)"/>;return <Stack screenOptions={{headerShown:false}}/>;}
