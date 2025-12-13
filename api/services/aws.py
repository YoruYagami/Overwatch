"""
AWS EC2 Infrastructure Provider

Implements the InfrastructureProvider interface for AWS EC2.
Handles EC2 instance lifecycle with auto-scaling support.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List

import aioboto3
from botocore.config import Config as BotoConfig

from config import settings
from .provider_interface import (
    InfrastructureProvider,
    ProviderType,
    VMInfo,
    ProviderQuota,
    ProviderError,
    ProviderCapacityError,
    ProviderTimeoutError,
    ProviderNotFoundError,
)

logger = logging.getLogger("vulnlab.aws")


class AWSProvider(InfrastructureProvider):
    """
    AWS EC2 infrastructure provider.

    Manages EC2 instance lifecycle with support for:
    - AMI-based instance launching
    - VPC and security group configuration
    - Elastic IP assignment (optional)
    - Instance tagging for tracking
    - Auto-scaling considerations
    """

    # Instance state mapping
    STATE_MAP = {
        "pending": "starting",
        "running": "running",
        "shutting-down": "stopping",
        "terminated": "terminated",
        "stopping": "stopping",
        "stopped": "stopped",
    }

    def __init__(
        self,
        region: str = None,
        access_key_id: str = None,
        secret_access_key: str = None,
        vpc_id: str = None,
        subnet_id: str = None,
        security_group_id: str = None,
    ):
        self.region = region or getattr(settings, 'aws_region', 'us-east-1')
        self.access_key_id = access_key_id or getattr(settings, 'aws_access_key_id', None)
        self.secret_access_key = secret_access_key or getattr(settings, 'aws_secret_access_key', None)
        self.vpc_id = vpc_id or getattr(settings, 'aws_vpc_id', None)
        self.subnet_id = subnet_id or getattr(settings, 'aws_subnet_id', None)
        self.security_group_id = security_group_id or getattr(settings, 'aws_security_group_id', None)

        # Boto3 configuration with retry logic
        self._boto_config = BotoConfig(
            region_name=self.region,
            retries={
                'max_attempts': 3,
                'mode': 'adaptive',
            },
            connect_timeout=10,
            read_timeout=30,
        )

        self._session = None
        self._lock = asyncio.Lock()

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.AWS

    def _get_session(self) -> aioboto3.Session:
        """Get or create aioboto3 session."""
        if self._session is None:
            self._session = aioboto3.Session(
                aws_access_key_id=self.access_key_id,
                aws_secret_access_key=self.secret_access_key,
                region_name=self.region,
            )
        return self._session

    async def health_check(self) -> bool:
        """Check AWS API accessibility."""
        try:
            session = self._get_session()
            async with session.client('ec2', config=self._boto_config) as ec2:
                await ec2.describe_regions()
            return True
        except Exception as e:
            logger.error(f"AWS health check failed: {e}")
            return False

    async def get_quota(self) -> ProviderQuota:
        """Get AWS EC2 service quotas and current usage."""
        try:
            session = self._get_session()
            async with session.client('ec2', config=self._boto_config) as ec2:
                # Get running instances
                response = await ec2.describe_instances(
                    Filters=[
                        {'Name': 'instance-state-name', 'Values': ['pending', 'running']},
                        {'Name': 'tag:ManagedBy', 'Values': ['vulnlab']},
                    ]
                )

                running_instances = []
                total_vcpus = 0
                for reservation in response.get('Reservations', []):
                    for instance in reservation.get('Instances', []):
                        running_instances.append(instance)
                        # Estimate vCPUs based on instance type
                        total_vcpus += self._get_vcpu_count(instance.get('InstanceType', 't3.micro'))

                # Get account limits (simplified - actual limits vary by instance type)
                # In production, use Service Quotas API
                return ProviderQuota(
                    max_instances=100,  # Default limit, configurable
                    current_instances=len(running_instances),
                    max_vcpus=256,  # Default vCPU limit
                    current_vcpus=total_vcpus,
                    max_memory_gb=512,
                    current_memory_gb=len(running_instances) * 4,  # Rough estimate
                )

        except Exception as e:
            logger.error(f"Failed to get AWS quota: {e}")
            raise ProviderError(str(e), self.provider_type)

    def _get_vcpu_count(self, instance_type: str) -> int:
        """Estimate vCPU count for instance type."""
        vcpu_map = {
            't3.micro': 2, 't3.small': 2, 't3.medium': 2, 't3.large': 2,
            't3.xlarge': 4, 't3.2xlarge': 8,
            'm5.large': 2, 'm5.xlarge': 4, 'm5.2xlarge': 8,
            'c5.large': 2, 'c5.xlarge': 4, 'c5.2xlarge': 8,
        }
        return vcpu_map.get(instance_type, 2)

    async def create_instance(
        self,
        template_id: str,  # AMI ID
        instance_name: str,
        instance_type: str = None,
        key_name: str = None,
        user_data: str = None,
        **kwargs,
    ) -> VMInfo:
        """
        Launch a new EC2 instance from an AMI.

        Args:
            template_id: AMI ID to launch from
            instance_name: Name tag for the instance
            instance_type: EC2 instance type (default: t3.small)
            key_name: SSH key pair name
            user_data: User data script (base64 encoded)
        """
        instance_type = instance_type or getattr(settings, 'aws_default_instance_type', 't3.small')

        try:
            session = self._get_session()
            async with session.client('ec2', config=self._boto_config) as ec2:
                # Build launch parameters
                params = {
                    'ImageId': template_id,
                    'InstanceType': instance_type,
                    'MinCount': 1,
                    'MaxCount': 1,
                    'TagSpecifications': [
                        {
                            'ResourceType': 'instance',
                            'Tags': [
                                {'Key': 'Name', 'Value': instance_name},
                                {'Key': 'ManagedBy', 'Value': 'vulnlab'},
                                {'Key': 'CreatedAt', 'Value': datetime.utcnow().isoformat()},
                            ],
                        },
                    ],
                }

                # Add optional parameters
                if self.subnet_id:
                    params['SubnetId'] = self.subnet_id
                if self.security_group_id:
                    params['SecurityGroupIds'] = [self.security_group_id]
                if key_name:
                    params['KeyName'] = key_name
                if user_data:
                    params['UserData'] = user_data

                logger.info(f"Launching EC2 instance from AMI {template_id}")

                response = await ec2.run_instances(**params)
                instance = response['Instances'][0]
                instance_id = instance['InstanceId']

                logger.info(f"Launched EC2 instance {instance_id}")

                return VMInfo(
                    provider=self.provider_type,
                    instance_id=instance_id,
                    name=instance_name,
                    status="starting",
                    created_at=datetime.utcnow(),
                    metadata={
                        'ami_id': template_id,
                        'instance_type': instance_type,
                        'availability_zone': instance.get('Placement', {}).get('AvailabilityZone'),
                    },
                )

        except Exception as e:
            if 'InsufficientInstanceCapacity' in str(e):
                raise ProviderCapacityError(self.provider_type)
            logger.error(f"Failed to create EC2 instance: {e}")
            raise ProviderError(f"Launch failed: {e}", self.provider_type)

    async def start_instance(self, instance_id: str) -> VMInfo:
        """Start a stopped EC2 instance."""
        try:
            session = self._get_session()
            async with session.client('ec2', config=self._boto_config) as ec2:
                await ec2.start_instances(InstanceIds=[instance_id])
                logger.info(f"Started EC2 instance {instance_id}")
                return await self.get_instance(instance_id)

        except Exception as e:
            logger.error(f"Failed to start EC2 instance {instance_id}: {e}")
            raise ProviderError(f"Start failed: {e}", self.provider_type)

    async def stop_instance(self, instance_id: str, force: bool = False) -> VMInfo:
        """Stop a running EC2 instance."""
        try:
            session = self._get_session()
            async with session.client('ec2', config=self._boto_config) as ec2:
                await ec2.stop_instances(
                    InstanceIds=[instance_id],
                    Force=force,
                )
                logger.info(f"Stopped EC2 instance {instance_id}")
                return await self.get_instance(instance_id)

        except Exception as e:
            logger.error(f"Failed to stop EC2 instance {instance_id}: {e}")
            raise ProviderError(f"Stop failed: {e}", self.provider_type)

    async def terminate_instance(self, instance_id: str) -> bool:
        """Terminate (delete) an EC2 instance."""
        try:
            session = self._get_session()
            async with session.client('ec2', config=self._boto_config) as ec2:
                await ec2.terminate_instances(InstanceIds=[instance_id])
                logger.info(f"Terminated EC2 instance {instance_id}")
                return True

        except Exception as e:
            logger.error(f"Failed to terminate EC2 instance {instance_id}: {e}")
            raise ProviderError(f"Terminate failed: {e}", self.provider_type)

    async def get_instance(self, instance_id: str) -> Optional[VMInfo]:
        """Get EC2 instance details."""
        try:
            session = self._get_session()
            async with session.client('ec2', config=self._boto_config) as ec2:
                response = await ec2.describe_instances(InstanceIds=[instance_id])

                if not response.get('Reservations'):
                    return None

                instance = response['Reservations'][0]['Instances'][0]
                state = instance['State']['Name']

                # Get name from tags
                name = instance_id
                for tag in instance.get('Tags', []):
                    if tag['Key'] == 'Name':
                        name = tag['Value']
                        break

                return VMInfo(
                    provider=self.provider_type,
                    instance_id=instance_id,
                    name=name,
                    status=self.STATE_MAP.get(state, state),
                    ip_address=instance.get('PrivateIpAddress'),
                    private_ip=instance.get('PrivateIpAddress'),
                    public_ip=instance.get('PublicIpAddress'),
                    created_at=instance.get('LaunchTime'),
                    metadata={
                        'instance_type': instance.get('InstanceType'),
                        'availability_zone': instance.get('Placement', {}).get('AvailabilityZone'),
                        'ami_id': instance.get('ImageId'),
                        'vpc_id': instance.get('VpcId'),
                        'subnet_id': instance.get('SubnetId'),
                    },
                )

        except Exception as e:
            if 'InvalidInstanceID.NotFound' in str(e):
                return None
            logger.error(f"Failed to get EC2 instance {instance_id}: {e}")
            return None

    async def list_instances(self, filters: Optional[Dict[str, Any]] = None) -> List[VMInfo]:
        """List EC2 instances managed by VulnLab."""
        try:
            session = self._get_session()
            async with session.client('ec2', config=self._boto_config) as ec2:
                ec2_filters = [
                    {'Name': 'tag:ManagedBy', 'Values': ['vulnlab']},
                    {'Name': 'instance-state-name', 'Values': ['pending', 'running', 'stopping', 'stopped']},
                ]

                # Add custom filters
                if filters:
                    if 'state' in filters:
                        ec2_filters = [f for f in ec2_filters if f['Name'] != 'instance-state-name']
                        ec2_filters.append({'Name': 'instance-state-name', 'Values': [filters['state']]})

                response = await ec2.describe_instances(Filters=ec2_filters)

                result = []
                for reservation in response.get('Reservations', []):
                    for instance in reservation.get('Instances', []):
                        # Get name from tags
                        name = instance['InstanceId']
                        for tag in instance.get('Tags', []):
                            if tag['Key'] == 'Name':
                                name = tag['Value']
                                break

                        result.append(VMInfo(
                            provider=self.provider_type,
                            instance_id=instance['InstanceId'],
                            name=name,
                            status=self.STATE_MAP.get(instance['State']['Name'], instance['State']['Name']),
                            ip_address=instance.get('PrivateIpAddress'),
                            private_ip=instance.get('PrivateIpAddress'),
                            public_ip=instance.get('PublicIpAddress'),
                            created_at=instance.get('LaunchTime'),
                            metadata={
                                'instance_type': instance.get('InstanceType'),
                            },
                        ))

                return result

        except Exception as e:
            logger.error(f"Failed to list EC2 instances: {e}")
            raise ProviderError(f"List failed: {e}", self.provider_type)

    async def reset_instance(self, instance_id: str, snapshot_name: str = "clean") -> VMInfo:
        """
        Reset EC2 instance by replacing root volume with snapshot.

        For EC2, this involves:
        1. Stop the instance
        2. Detach current root volume
        3. Create new volume from snapshot
        4. Attach new volume as root
        5. Start the instance
        """
        try:
            session = self._get_session()
            async with session.client('ec2', config=self._boto_config) as ec2:
                # Get instance details
                instance = await self.get_instance(instance_id)
                if not instance:
                    raise ProviderNotFoundError(self.provider_type, "instance", instance_id)

                # Stop instance if running
                if instance.status == "running":
                    await self.stop_instance(instance_id)
                    await self._wait_for_state(instance_id, "stopped")

                # Get root volume
                response = await ec2.describe_instances(InstanceIds=[instance_id])
                instance_data = response['Reservations'][0]['Instances'][0]

                root_device = instance_data.get('RootDeviceName', '/dev/xvda')
                root_volume_id = None
                availability_zone = instance_data['Placement']['AvailabilityZone']

                for mapping in instance_data.get('BlockDeviceMappings', []):
                    if mapping['DeviceName'] == root_device:
                        root_volume_id = mapping['Ebs']['VolumeId']
                        break

                if not root_volume_id:
                    raise ProviderError("Could not find root volume", self.provider_type)

                # Find snapshot with the specified name tag
                snapshot_response = await ec2.describe_snapshots(
                    Filters=[
                        {'Name': 'tag:Name', 'Values': [snapshot_name]},
                        {'Name': 'status', 'Values': ['completed']},
                    ],
                    OwnerIds=['self'],
                )

                snapshots = snapshot_response.get('Snapshots', [])
                if not snapshots:
                    raise ProviderError(f"Snapshot '{snapshot_name}' not found", self.provider_type)

                snapshot_id = snapshots[0]['SnapshotId']

                # Detach current root volume
                await ec2.detach_volume(
                    VolumeId=root_volume_id,
                    InstanceId=instance_id,
                    Force=True,
                )

                # Wait for detachment
                await asyncio.sleep(10)

                # Create new volume from snapshot
                new_volume = await ec2.create_volume(
                    SnapshotId=snapshot_id,
                    AvailabilityZone=availability_zone,
                    VolumeType='gp3',
                    TagSpecifications=[
                        {
                            'ResourceType': 'volume',
                            'Tags': [
                                {'Key': 'Name', 'Value': f'{instance.name}-root'},
                                {'Key': 'ManagedBy', 'Value': 'vulnlab'},
                            ],
                        },
                    ],
                )

                new_volume_id = new_volume['VolumeId']

                # Wait for volume to be available
                waiter = ec2.get_waiter('volume_available')
                await waiter.wait(VolumeIds=[new_volume_id])

                # Attach new volume as root
                await ec2.attach_volume(
                    VolumeId=new_volume_id,
                    InstanceId=instance_id,
                    Device=root_device,
                )

                await asyncio.sleep(5)

                # Delete old volume
                await ec2.delete_volume(VolumeId=root_volume_id)

                # Start instance
                await self.start_instance(instance_id)

                logger.info(f"Reset EC2 instance {instance_id} from snapshot {snapshot_name}")
                return await self.get_instance(instance_id)

        except ProviderError:
            raise
        except Exception as e:
            logger.error(f"Failed to reset EC2 instance {instance_id}: {e}")
            raise ProviderError(f"Reset failed: {e}", self.provider_type)

    async def _wait_for_state(self, instance_id: str, target_state: str, timeout: int = 300):
        """Wait for instance to reach target state."""
        start_time = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() - start_time < timeout:
            instance = await self.get_instance(instance_id)
            if instance and instance.status == target_state:
                return
            await asyncio.sleep(5)

        raise ProviderTimeoutError(self.provider_type, f"wait_for_{target_state}")

    async def wait_for_ip(self, instance_id: str, timeout: int = 300) -> Optional[str]:
        """Wait for instance to get an IP address."""
        start_time = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() - start_time < timeout:
            instance = await self.get_instance(instance_id)
            if instance:
                ip = instance.private_ip or instance.public_ip
                if ip:
                    return ip
            await asyncio.sleep(5)

        logger.warning(f"Timeout waiting for IP on EC2 instance {instance_id}")
        return None

    async def list_templates(self) -> List[Dict[str, Any]]:
        """List available AMIs owned by the account or shared."""
        try:
            session = self._get_session()
            async with session.client('ec2', config=self._boto_config) as ec2:
                # Get AMIs with vulnlab tag
                response = await ec2.describe_images(
                    Owners=['self'],
                    Filters=[
                        {'Name': 'tag:ManagedBy', 'Values': ['vulnlab']},
                        {'Name': 'state', 'Values': ['available']},
                    ],
                )

                templates = []
                for image in response.get('Images', []):
                    name = image.get('Name', image['ImageId'])
                    for tag in image.get('Tags', []):
                        if tag['Key'] == 'Name':
                            name = tag['Value']
                            break

                    templates.append({
                        'template_id': image['ImageId'],
                        'name': name,
                        'description': image.get('Description', ''),
                        'architecture': image.get('Architecture'),
                        'platform': image.get('PlatformDetails'),
                        'created_at': image.get('CreationDate'),
                    })

                return templates

        except Exception as e:
            logger.error(f"Failed to list AMIs: {e}")
            raise ProviderError(f"List templates failed: {e}", self.provider_type)

    # Legacy compatibility methods
    async def start_machine(self, template_id: str, instance_id: int, **kwargs) -> Dict[str, Any]:
        """Legacy method for backward compatibility."""
        vm_info = await self.create_instance(
            template_id=template_id,
            instance_name=f"vulnlab-instance-{instance_id}",
            **kwargs,
        )
        ip = await self.wait_for_ip(vm_info.instance_id, timeout=120)
        return {"vmid": vm_info.instance_id, "ip": ip}

    async def stop_machine(self, instance_id: str, **kwargs) -> None:
        """Legacy method for backward compatibility."""
        await self.terminate_instance(instance_id)

    async def reset_machine(self, instance_id: str, **kwargs) -> None:
        """Legacy method for backward compatibility."""
        await self.reset_instance(instance_id)


# Alias for consistency
AWSService = AWSProvider
