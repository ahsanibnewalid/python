import {Image,StyleSheet} from "react-native";
import {useVideoPlayer,VideoView} from "expo-video";
function RemoteVideo({uri}:{uri:string}){const player=useVideoPlayer(uri,p=>{p.loop=false;});return <VideoView player={player} style={styles.video} contentFit="cover" nativeControls/>;}
export function MediaCard({uri,type,height=300}:{uri?:string|null;type:"image"|"video";height?:number}){if(!uri)return null;if(type==="video")return <RemoteVideo uri={uri}/>;return <Image source={{uri}} style={[styles.image,{height}]} resizeMode="cover"/>;}
const styles=StyleSheet.create({image:{width:"100%",height:300,borderRadius:16,backgroundColor:"#101828"},video:{width:"100%",height:300,borderRadius:16,backgroundColor:"#101828"}});
