const configured = process.env.EXPO_PUBLIC_API_URL ? process.env.EXPO_PUBLIC_API_URL.trim() : "";
export const API_URL = (configured || "http://127.0.0.1:5000").replace(/\/$/, "");
export const API_V1 = API_URL + "/api/v1";
export const MAX_UPLOAD_MB = 512;
