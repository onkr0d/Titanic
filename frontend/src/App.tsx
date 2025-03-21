import FileUploader from "./components/FileUploader";

// Import the functions you need from the SDKs you need
import { initializeApp } from "firebase/app";
// TODO: Add SDKs for Firebase products that you want to use
// https://firebase.google.com/docs/web/setup#available-libraries

// Your web app's Firebase configuration
const firebaseConfig = {
  apiKey: "how'd that get here",
  authDomain: "oops",
  projectId: "probably not critical",
  storageBucket: "probably not critical.firebasestorage.app",
  messagingSenderId: "yikes",
  appId: "1:yikes:web:419dffceedf0eaaddb4fd8"
};

// Initialize Firebase
// const app = 
initializeApp(firebaseConfig);

const App = () => {
    return (
        <div className="min-h-screen w-full flex items-center justify-center">
            <FileUploader/>
        </div>
    );
};

export default App;