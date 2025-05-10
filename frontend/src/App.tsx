import { AuthError, User } from "firebase/auth";
import { onAuthStateChanged, GoogleAuthProvider, signInWithPopup } from "firebase/auth";
import { getAuth, connectAuthEmulator} from "firebase/auth";
import FileUploader from "./components/FileUploader";

// Import the functions you need from the SDKs you need
import { useState } from "react";
import { initializeApp } from "firebase/app";
import { useEffect } from "react";
import { Slide, toast, ToastContainer } from "react-toastify";
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
            const result = await signInWithPopup(auth, provider);
            console.log("User email:", result.user.email);
            toast.success("Signed in with Google", {
                position: "top-right",
                autoClose: 5000,
                hideProgressBar: false,
                closeOnClick: true,
                pauseOnFocusLoss: false,
                pauseOnHover: false,
                draggable: true,
                progress: undefined,
                theme: window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light',
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
                        errorMessage = "Server error: " + match[1];
                    }
                } catch (e) {
                    console.error("Error parsing error message:", e);
                }
            }

            toast.error(errorMessage, {
                position: "top-right",
                autoClose: 5000,
                hideProgressBar: false,
                closeOnClick: true,
                pauseOnFocusLoss: false,
                pauseOnHover: false,
                draggable: true,
                progress: undefined,
                theme: window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light',
                transition: Slide,
            });
        }
    };

// getAuth().signOut();

    if (!user) {
        return (
            <div>
            <ToastContainer draggablePercent={60}/>
            <div className="min-h-screen w-full flex items-center justify-center">
                <button
                    onClick={signInWithGoogle}
                    className="flex items-center gap-2 bg-white text-gray-700 px-6 py-3 rounded-lg border border-gray-300 hover:bg-gray-50 transition-colors shadow-sm"
                >
                <img src="/google-icon-logo-svgrepo-com.svg" alt="Google" className="w-5 h-5" />
                    Continue with Google
                </button>
            </div>
            </div>
        );
    }

    return (
        <div>
            <ToastContainer draggablePercent={60}/>
            <div className="min-h-screen w-full flex items-center justify-center">
                <FileUploader/>
            </div>
        </div>
    );
};

export default App;