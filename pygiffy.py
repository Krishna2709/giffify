from moviepy import VideoFileClip
from PIL import Image

# Define the name of your input video file
input_video = "/Users/krishna/Downloads/WhatsApp Video 2026-02-14 at 10.16.02.mp4" 

# Define the name for your output GIF file
output_gif = "dn_v1.gif"

# Define the start and end times for the 8-second clip (e.g., the first 8 seconds)
# You can adjust these times to select any 8-second segment of a longer video
start_time = 0 # Start at the beginning (0 seconds)
end_time = 8   # End at 8 seconds

# Load the video file
clip = VideoFileClip(input_video)

# Select the desired 8-second subclip
# The .subclip() method takes start and end times in seconds or (minutes, seconds)
eight_sec_clip = clip.subclipped(start_time, end_time)

# Write the clip to a GIF file using Pillow (moviepy's write_gif is bugged in v2.x)
fps = 15
frames = []
for frame in eight_sec_clip.iter_frames(fps=fps, dtype="uint8"):
    frames.append(Image.fromarray(frame))

frames[0].save(
    output_gif,
    save_all=True,
    append_images=frames[1:],
    loop=0,
    duration=int(1000 / fps),
)

# Close the clips
eight_sec_clip.close()
clip.close()

print(f"Successfully converted '{input_video}' (0-{end_time}s) to '{output_gif}'")
