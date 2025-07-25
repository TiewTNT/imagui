from fastapi import FastAPI, Form, File, UploadFile, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from typing import List
from call import *
from file import *
from utils import *
from werkzeug.utils import secure_filename
import json
import asyncio
import psutil
import time
import shlex

mem = psutil.virtual_memory()

app = FastAPI()


# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins="*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def process_file(f, input_format, output_format, tools):
    mem = psutil.virtual_memory()
    start = time.time()
    while mem.available < 1024*1024*24:
        mem = psutil.virtual_memory()
        if time.time() - start > 30:
            return JSONResponse(
                status_code=503,
                content={"error": "Server is busy",
                         "message": "The inconveniences of running a potato farm... Try waiting about 15 seconds for the spud to cool down."}
            )
        await asyncio.sleep(3)
        # Chill...!
    try:
        assert type(f) == TempFile

        buffer = await asyncio.to_thread(
            MagickBuffer, input_file=f.path, input_format=input_format, output_format=output_format
        )

        if type(tools) == str and len(tools) > 2:  # Check if use specified tools
            tools_dict = json.loads(tools)
            flags_pre_input = []
            flags = []

            print(
                f"[DEBUG] Saved {f.path}, size={Path(f.path).stat().st_size}")
            with open(f.path, 'rb') as file:
                print(f"[DEBUG] First bytes: {file.read(8)}")

            pre_input = tools_dict["gb"] + tools_dict["pi"]
            after_input = tools_dict["in"] + tools_dict["po"]
            for flag in pre_input:
                flag = parse(flag)
                
                print('[DEBUG] Using', flag)
                if type(flag) == str:
                    if flag:
                        flags_pre_input.extend(shlex.split(flag))
                else:
                    flags_pre_input.extend([v for v in flag if v])
            for flag in after_input:
                flag = parse(flag)

                print('[DEBUG] Using', flag)
                if type(flag) == str:
                    if flag:
                        flags.extend(shlex.split(flag))
                else:
                    flags.extend([v for v in flag if v])

            await asyncio.to_thread(buffer.cmd, flags, flags_pre_input)

        if output_format:
            saved_file = TempFile(suffix=output_format)
        else:
            saved_file = TempFile(suffix=get_image_format(f.path))

        await asyncio.to_thread(buffer.save, saved_file.path)

        return saved_file
    except Exception as e:
        print(f'[ERROR] File {f.path} not processed properly.\n       → {e}')
        return None


# Main API endpoint

@app.post("/api")
async def api(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(),
    input_format: str | None = Form(None),
    output_format: str | None = Form(None),
    tools: str | None = Form(None)
):

    # File writing and size limiting
    size = 0
    size_limit = 33554432

    user_files = []

    for f in files:
        if (secure_filename(f.filename) != f.filename) or not Path(f.filename).stem:
            print(
                f"[SECURITY] Using other name for file with unsafe name: {f.filename}")
        fname = secure_filename(f.filename)

        if input_format:
            format_ext_dict = {
                "png": "png",
                "jpg": "jpg",
                "gif": "gif",
                "webp": "webp",
                "avif": "avif"
            }
            input_ext = format_ext_dict[input_format.lower()]
            temp_file = TempFile(suffix=input_ext)
            print('[INFO] Created input TempFile with suffix', input_ext)
        else:
            temp_file = TempFile(suffix=get_image_format(Path(fname)))
            print('[INFO] Created TempFile with suffix',
                  get_image_format(Path(fname)))

        print('[INFO] User specifies output format:', str(output_format))
        user_files.append(temp_file)

        while True:
            chunk = await f.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > size_limit:
                return JSONResponse(
                    status_code=413,
                    content={
                        "error": "File too large", "message": f"Uploads are limited to around {size_limit/(1024**2)}MB total."}
                )
            temp_file.append_bytes(chunk)

    # For processed files
    saved_files = []

    # Cleanup
    background_tasks.add_task(delete_all)

    tasks = [
        process_file(f, input_format, output_format, tools)
        for f in user_files
    ]
    results = await asyncio.gather(*tasks)
    saved_files = [r for r in results if r is not None]

    # In case no files were actually processed
    if len(saved_files) == 0:
        return JSONResponse(
            status_code=500,
            content={
                "error": "No files were processed", "message": f"Error most likely occurred in Magick buffer stream"}
        )
    elif len(saved_files) == 1:  # If there's only one file, just return it
        return FileResponse(saved_files[0].path, filename=str(Path(files[0].filename).with_suffix(saved_files[0].path.suffix)))
    else:  # If there are multiple files, return a .zip archive
        with zip_temps(saved_files, files, background_tasks) as zipped_path:
            background_tasks.add_task(zipped_path.unlink)
            return FileResponse(zipped_path, filename=f"{Path(files[0].filename).with_suffix('.zip')}", media_type="application/zip")
