import cv2
import time

class Camera:
    def __init__(self, device_index=0):
        self.device_index = device_index
        self.camera = cv2.VideoCapture(device_index)
        if not self.camera.isOpened():
            raise RuntimeError(f"Unable to open video device {device_index}")
    
    def __del__(self):
        if self.camera.isOpened():
            self.camera.release()
    
    def get_frame(self):
        success, frame = self.camera.read()
        if not success:
            raise RuntimeError("Failed to capture frame from camera.")
        
        # Encode the frame to JPEG format for streaming because current format is not supported for streaming
        ret, buffer = cv2.imencode('.jpg', frame)
        if not ret:
            raise RuntimeError("Failed to encode frame as JPEG.")
        
        return buffer.tobytes() 


if __name__ == '__main__':
    try:
        print("Initializing camera...")
        camera = Camera(device_index=0)
        
        print("Starting camera test. Capturing frames...")
        print("Press Ctrl+C to stop.")

        frame_count = 0
        start_time = time.time()

        while True:
            frame = camera.get_frame() 
            frame_count += 1

            # Save a frame every 5 seconds (for debugging purposes)
            if frame_count % 150 == 0:
                filename = f"frame_{frame_count}.jpg"
                cv2.imwrite(filename, frame)
                print(f"Saved: {filename}")

            elapsed_time = time.time() - start_time
            print(f"Captured frame {frame_count} | Elapsed time: {elapsed_time:.2f}s", end="\r")

            time.sleep(0.03)

    except KeyboardInterrupt:
        print("\nCamera test interrupted by user.")
    except RuntimeError as e:
        print(f"Error: {e}")
    finally:
        print("\nReleasing camera resources...")
        del camera
        print("Camera test ended.")
