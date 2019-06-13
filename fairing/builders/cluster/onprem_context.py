import os

from fairing import utils
from fairing.constants import constants
from fairing.kubernetes.manager import client, KubeManager
from fairing.builders.cluster.context_source import ContextSourceInterface
from minio import Minio
from minio.error import (ResponseError, BucketAlreadyOwnedByYou,
                         BucketAlreadyExists)


class OnPremContextSource(ContextSourceInterface):
    def __init__(self,
                 gcp_project=None,
                 registry_creds=None,
                 credentials_file=os.environ.get(constants.GOOGLE_CREDS_ENV),
                 namespace='default'):
        self.gcp_project = gcp_project
        self.credentials_file = credentials_file
        self.manager = KubeManager()
        self.namespace = namespace
        self._registry_creds = registry_creds
        self.uploaded_context_url = ""
        self.context_name = "build_context.tar.gz"
        self.minioClient = Minio('minio:9000',
                                 access_key='minio',
                                 secret_key='minio123',
                                 secure=False)

    def prepare(self, context_filename):
        if self.gcp_project is None:
            self.gcp_project = 'fairing'
        self.uploaded_context_url, self.context_name = self.upload_context(
            context_filename)

    def upload_context(self, context_filename):
        context_hash = utils.crc(context_filename)
        try:
            self.minioClient.make_bucket(self.gcp_project)
        except BucketAlreadyOwnedByYou as err:
            print('BucketAlreadyOwnedByYou')
            pass
        except BucketAlreadyExists as err:
            print('BucketAlreadyExists')
            pass
        except ResponseError as err:
            print('ResponseError')
            raise
        # Put an object 'pumaserver_debug.log' with contents from 'pumaserver_debug.log'.
        try:
            context_name = context_hash + '.tar.gz'
            self.minioClient.fput_object(self.gcp_project,
                                         'fairing_builds/' + context_name,
                                         context_filename)
            s3_url = "s3://" + self.gcp_project + "/fairing_builds/" + context_name
        except ResponseError as err:
            print(err)
        return s3_url, context_name

    def cleanup(self):
        pass

    def generate_pod_spec(self, image_name, push):
        args = ["--dockerfile=Dockerfile",
                "--destination=" + image_name,
                "--context=dir://build_context/" + self.context_name]
        if not push:
            args.append("--no-push")
        return client.V1PodSpec(
            init_containers=[client.V1Container(
                name='minio-s3-pulling',
                image='registry.dudaji.org/dudaji/s3cmd:latest',
                args=[self.upload_context_url, "/build_context"],
                volume_mounts=[
                    client.V1VolumeMount(
                        name="build-context",
                        mount_path="/build_context"
                    )
                ]
            )],
            containers=[client.V1Container(
                name='kaniko',
                image='gcr.io/kaniko-project/executor:v0.7.0',
                args=["--dockerfile=/build_context/Dockerfile",
                      "--destination=" + image_name,
                      "--context=dir:///build_context/"],
                volume_mounts=[
                    client.V1VolumeMount(
                        name="build-context",
                        mount_path="/build_context"
                    ),
                    client.V1VolumeMount(
                        name="registry-creds",
                        mount_path="/root"
                    )
                ]
            )],
            restart_policy='Never',
            volumes=[
                client.V1Volume(
                    name="build-context",
                    empty_dir=client.V1EmptyDirVolumeSource()
                ),
                client.V1Volume(
                    name="registry-creds",
                    projected=client.V1ProjectedVolumeSource(
                        sources=[client.V1VolumeProjection(
                            secret=client.V1SecretProjection(
                                name=self._registry_creds,
                                items=[client.V1KeyToPath(
                                    key=".dockerconfigjson",
                                    path=".docker/config.json"
                                )
                                ]
                            )
                        )]

                    )
                )

            ]
        )
