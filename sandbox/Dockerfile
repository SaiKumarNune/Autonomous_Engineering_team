FROM python:3.12-slim

# Packages are installed only while the image is built.
# The workflow does not run pip, and the container has no network.
COPY preapproved-requirements.txt /tmp/preapproved-requirements.txt
RUN pip install --no-cache-dir -r /tmp/preapproved-requirements.txt \
    && pip uninstall -y pip \
    && rm -rf /tmp/preapproved-requirements.txt /root/.cache/pip

WORKDIR /workspace
