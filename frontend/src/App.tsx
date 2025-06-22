import { User } from "firebase/auth";
import { onAuthStateChanged, GoogleAuthProvider, signInWithPopup } from "firebase/auth";
import { getAuth, connectAuthEmulator } from "firebase/auth";
import FileUploader from "./components/FileUploader";
import BackendStatus from "./components/BackendStatus";
import DiskSpaceIndicator from "./components/DiskSpaceIndicator";

// Import the functions you need from the SDKs you need
import { useState } from "react";
import { initializeApp } from "firebase/app";
import { useEffect } from "react";
import { Slide, ToastContainer } from "react-toastify";
import { showToast } from "./utils/toast";
// TODO: Add SDKs for Firebase products that you want to use
// https://firebase.google.com/docs/web/setup#available-libraries

// Your web app's Firebase configuration
const firebaseConfig = {
    apiKey: "AIzaSyBjv6oD7C_D7U3H_y1SbAnoJSrK_MWDTjY",
    authDomain: "titanic-uploader.firebaseapp.com",
    projectId: "titanic-uploader",
    storageBucket: "titanic-uploader.firebasestorage.app",
    messagingSenderId: "642549593353",
    appId: "1:642549593353:web:419dffceedf0eaaddb4fd8"
};

// Initialize Firebase
// const app = 
initializeApp(firebaseConfig);

const App = () => {
    const [user, setUser] = useState<User | null>(null);

    useEffect(() => {
        const auth = getAuth();
        // use emulator if in dev
        const isDev = process.env.NODE_ENV === "development";
        if (isDev) {
            connectAuthEmulator(auth, "http://127.0.0.1:9099");
        }
        onAuthStateChanged(auth, (user) => {
            setUser(user);
        });
    }, []);

    const signInWithGoogle = async () => {
        const auth = getAuth();
        const provider = new GoogleAuthProvider();
        try {
            await signInWithPopup(auth, provider);
            showToast.success("Signed in with Google", {
                transition: Slide,
            });
        } catch (error: any) {
            let errorMessage = "Error signing in with Google";

            // Check if it's a Firebase Function error
            if (error.code === 'auth/internal-error' && error.message.includes('HTTP error 403')) {
                try {
                    // Extract the error message from the function response
                    const match = error.message.match(/{"error":{"message":"([^"]+)"/);
                    if (match) {
                        errorMessage += ": " + match[1];
                    }
                } catch (e) {
                    console.error("Error parsing error message:", e);
                }
            }

            showToast.error(errorMessage, {
                transition: Slide,
            });
        }
    };

    // getAuth().signOut();

    if (!user) {
        return (
            <div className="bg-white dark:bg-gray-900 min-h-screen">
                <ToastContainer draggablePercent={60} />
                <div className="min-h-screen w-full flex items-center justify-center">
                    <button
                        onClick={signInWithGoogle}
                        className="flex items-center gap-2 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-200 px-6 py-3 rounded-lg border border-gray-300 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors shadow-sm"
                    >
                        <img src="/google-icon-logo-svgrepo-com.svg" alt="Google" className="w-5 h-5" />
                        Continue with Google
                    </button>
                </div>
            </div>
        );
    }

    return (
        <div className="bg-white dark:bg-gray-900 min-h-screen">
            <ToastContainer draggablePercent={60} />
            <div className="min-h-screen w-full flex items-center justify-center">
                <FileUploader />
            </div>
            <div className="fixed bottom-4 left-4 right-4 md:left-auto md:right-4 z-10">
                <div className="md:hidden space-y-2">
                    <BackendStatus />
                    <DiskSpaceIndicator />
                </div>
                <div className="hidden md:block">
                    <BackendStatus />
                    <DiskSpaceIndicator />
                </div>
            </div>
        </div>
    );
};

export default App;