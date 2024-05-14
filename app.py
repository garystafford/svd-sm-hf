import base64
import json
import os
import time
import urllib.parse
from io import BytesIO

import boto3
import ffmpeg
import streamlit as st
from PIL import Image
from botocore.exceptions import ClientError


# Author: Gary A. Stafford
# Purpose: Call an Amazon SageMaker Asynchronous Inference endpoint to generate a short video from image using SVD-XT 1.1 image-to-video model
# Date: 2024-05-13
# License: MIT License

# Constants
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB

# AWS Configuration - *** CHANGE ME! ***
S3_BUCKET = "<YOUR_S3_BUCKET_NAME>"
ENDPOINT_NAME = "<YOUR_SAGEMAKER_ENDPOINT_NAME>"

# Local File Paths
REQUEST_PAYLOAD = "request_payloads/payload.json"
RESPONSE_PAYLOAD = "response_payloads/output.json"

s3_client = boto3.client("s3")


def main():
    st.set_page_config(
        page_title="Generative Video Creation",
        page_icon="",
    )

    custom_css = """
    <style>
            @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400&display=swap');
            html, body, p, li, a, h1, h2, h3, h4, h5, h6, table, td, th, div, form, input, button, textarea, [class*="css"] {
                font-family: 'Inter', sans-serif;
            }
    </style>
        """

    st.markdown(
        custom_css,
        unsafe_allow_html=True,
    )

    st.markdown("# Generative Video Creation")
    st.markdown("### Stable Video Diffusion XT 1.1 Image-to-Video")
    st.markdown("---")

    reset_session_states()

    my_upload = st.file_uploader(
        "Upload a Conditioning Image", type=["png", "jpg", "jpeg"]
    )

    if my_upload is not None:
        if my_upload.size > MAX_FILE_SIZE:
            st.error(
                "The uploaded file is too large. Please upload an image smaller than 5MB."
            )
        else:
            st.session_state["movie_title"] = os.path.splitext(my_upload.name)[0]
            image = Image.open(my_upload)
            st.session_state["width"], st.session_state["height"] = image.size
            display_image(image)

    movie_title = st.text_input(
        label="Movie Title", value=st.session_state.get("movie_title", "")
    )
    st.session_state["movie_title"] = movie_title

    video_path = f"video_out/{st.session_state.get('movie_title', '')}.mp4"

    width = st.number_input(label="Width", value=st.session_state.get("width", 1024))
    st.session_state["width"] = width

    height = st.number_input(label="Height", value=st.session_state.get("height", 576))
    st.session_state["height"] = height

    num_frames = st.number_input(
        label="Number of Frames", value=st.session_state.get("num_frames", 25)
    )
    st.session_state["num_frames"] = num_frames

    num_inference_steps = st.number_input(
        label="Number of Inference Steps",
        value=st.session_state.get("num_inference_steps", 25),
    )
    st.session_state["num_inference_steps"] = num_inference_steps

    min_guidance_scale = st.number_input(
        label="Minimum Guidance Scale",
        value=st.session_state.get("min_guidance_scale", 1.0),
    )
    st.session_state["min_guidance_scale"] = min_guidance_scale

    max_guidance_scale = st.number_input(
        label="Maximum Guidance Scale",
        value=st.session_state.get("max_guidance_scale", 3.0),
    )
    st.session_state["max_guidance_scale"] = max_guidance_scale

    fps = st.number_input(
        label="Frames per Second", value=st.session_state.get("fps", 6)
    )
    st.session_state["fps"] = fps

    motion_bucket_id = st.number_input(
        label="Motion Bucket ID", value=st.session_state.get("motion_bucket_id", 127)
    )
    st.session_state["motion_bucket_id"] = motion_bucket_id

    noise_aug_strength = st.number_input(
        label="Noise Augmentation Strength",
        value=st.session_state.get("noise_aug_strength", 0.02),
    )
    st.session_state["noise_aug_strength"] = noise_aug_strength

    decode_chunk_size = st.number_input(
        label="Decode Chunk Size", value=st.session_state.get("decode_chunk_size", 8)
    )
    st.session_state["decode_chunk_size"] = decode_chunk_size

    seed = st.number_input(label="Seed", value=st.session_state.get("seed", 42))
    st.session_state["seed"] = seed

    create_video = st.button("Create Video")

    if create_video:
        with st.spinner("Creating video..."):
            response_location = invoke_model(my_upload)
            get_model_response(response_location)

            with open(RESPONSE_PAYLOAD, "r") as f:
                data = json.load(f)
            save_video_frames(data["frames"])
            convert_frames_to_video(video_path)
            st.success(f"Video created: {video_path}")

    play_video(video_path)


def reset_session_states():
    session_state_defaults = {
        "play_video_disabled": True,
        "invocation_time": 0,
        "movie_title": None,
        "width": 1024,
        "height": 576,
        "num_frames": 25,
        "num_inference_steps": 25,
        "min_guidance_scale": 1.0,
        "max_guidance_scale": 3.0,
        "fps": 6,
        "motion_bucket_id": 127,
        "noise_aug_strength": 0.02,
        "decode_chunk_size": 8,
        "seed": 42,
    }

    for key, value in session_state_defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def upload_file(input_location):
    s3_client.upload_file(
        Filename=input_location,
        Bucket=S3_BUCKET,
        Key="async_inference/input/payload.json",
        ExtraArgs={"ContentType": "application/json"},
    )


def display_image(image):
    st.write("Conditioning Image :camera:")
    st.image(image, width=400)


def encode_image(image):
    buffered = BytesIO()
    image.save(buffered, format="JPEG")
    return base64.b64encode(buffered.getvalue()).decode("utf-8")


def invoke_model(upload):
    image = Image.open(upload)

    data = {
        "image": encode_image(image),
        "width": st.session_state["width"],
        "height": st.session_state["height"],
        "num_frames": st.session_state["num_frames"],
        "num_inference_steps": st.session_state["num_inference_steps"],
        "min_guidance_scale": st.session_state["min_guidance_scale"],
        "max_guidance_scale": st.session_state["max_guidance_scale"],
        "fps": st.session_state["fps"],
        "motion_bucket_id": st.session_state["motion_bucket_id"],
        "noise_aug_strength": st.session_state["noise_aug_strength"],
        "decode_chunk_size": st.session_state["decode_chunk_size"],
        "seed": st.session_state["seed"],
    }

    with open(REQUEST_PAYLOAD, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    upload_file(REQUEST_PAYLOAD)

    sm_runtime_client = boto3.client("sagemaker-runtime")

    response = sm_runtime_client.invoke_endpoint_async(
        EndpointName=ENDPOINT_NAME,
        InputLocation=f"s3://{S3_BUCKET}/async_inference/input/payload.json",
        InvocationTimeoutSeconds=3600,
    )

    return response["OutputLocation"]


def get_model_response(response_location):
    output_url = urllib.parse.urlparse(response_location)
    bucket = output_url.netloc
    key = output_url.path[1:]
    while True:
        try:
            s3_client.get_object(Bucket=bucket, Key=key)
            s3_client.download_file(Bucket=bucket, Key=key, Filename=RESPONSE_PAYLOAD)
            print("Model response received.")
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                print("Waiting for model output...")
                time.sleep(15)
                continue
            raise


def save_video_frames(video_frames):
    for idx, video_frame in enumerate(video_frames):
        frame = bytes(video_frame, encoding="raw_unicode_escape")

        frame_name = (
            f"frames_out/frame_0{idx+1}.jpg"
            if idx < 9
            else f"frames_out/frame_{idx+1}.jpg"
        )
        with open(frame_name, "wb") as fh:
            fh.write(base64.decodebytes(frame))


def convert_frames_to_video(video_path):
    output_options = {
        "crf": 20,
        "preset": "slower",
        "movflags": "faststart",
        "pix_fmt": "yuv420p",
        "vcodec": "libx264",
    }

    (
        ffmpeg.input(
            filename="frames_out/*.jpg",
            pattern_type="glob",
            framerate=st.session_state["fps"],
        )
        .output(video_path, **output_options)
        .run(overwrite_output=True, quiet=True)
    )


def play_video(video_path):
    st.session_state["play_video_disabled"] = False
    play_video_button = st.button(
        "Play Video", disabled=st.session_state.get("play_video_disabled", False)
    )

    if play_video_button:
        try:
            video_file = open(video_path, "rb")
            video_bytes = video_file.read()
            st.video(video_bytes, format="video/mp4", loop=True, autoplay=True)
        except FileNotFoundError:
            st.warning(
                "Video does not exist. Please create the video first.   :warning:"
            )


if __name__ == "__main__":
    main()
