from rest_framework import status, views, permissions, generics
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from .models import User, CustomerProfile, StaffProfile
from .serializers import UserSerializer, RegisterSerializer, LoginSerializer
from .permissions import IsAdmin, IsStaffOrAdmin


def get_tokens_for_user(user):
    refresh = RefreshToken.for_user(user)
    return {
        "refresh": str(refresh),
        "access": str(refresh.access_token),
    }


class RegisterView(views.APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            tokens = get_tokens_for_user(user)
            user_data = UserSerializer(user).data
            return Response(
                {
                    "message": "Registration successful",
                    "user": user_data,
                    "tokens": tokens,
                },
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class LoginView(views.APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.validated_data["user"]
            tokens = get_tokens_for_user(user)
            user_data = UserSerializer(user).data
            return Response(
                {
                    "message": "Login successful",
                    "user": user_data,
                    "tokens": tokens,
                },
                status=status.HTTP_200_OK,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class CurrentUserView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        serializer = UserSerializer(request.user)
        return Response(serializer.data)

    def put(self, request):
        user = request.user
        data = request.data
        if "first_name" in data:
            user.first_name = data["first_name"]
        if "last_name" in data:
            user.last_name = data["last_name"]
        if "phone" in data:
            user.phone = data["phone"]
        user.save()

        # Update customer profile if birthday provided
        if hasattr(user, "customer_profile") and "birthday" in data:
            profile = user.customer_profile
            profile.birthday = data["birthday"] or None
            profile.save()

        return Response(UserSerializer(user).data)


class AdminCustomerListView(views.APIView):
    permission_classes = [IsStaffOrAdmin]

    def get(self, request):
        customers = (
            User.objects.filter(role="CUSTOMER")
            .select_related("customer_profile")
            .order_by("-date_joined")
        )
        search = request.query_params.get("search", "").strip()
        if search:
            customers = customers.filter(
                models.Q(email__icontains=search)
                | models.Q(first_name__icontains=search)
                | models.Q(last_name__icontains=search)
                | models.Q(phone__icontains=search)
            )
        serializer = UserSerializer(customers, many=True)
        return Response(serializer.data)


class AdminStaffView(views.APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        staff_members = User.objects.filter(role__in=["STAFF", "ADMIN"]).select_related(
            "staff_profile"
        )
        serializer = UserSerializer(staff_members, many=True)
        return Response(serializer.data)

    def post(self, request):
        data = request.data.copy()
        data["role"] = data.get("role", "STAFF")
        serializer = RegisterSerializer(data=data)
        if serializer.is_valid():
            user = serializer.save()
            # Update staff profile details
            if hasattr(user, "staff_profile"):
                staff_prof = user.staff_profile
                staff_prof.employee_id = data.get("employee_id", "")
                staff_prof.department = data.get("department", "Turf Operations")
                staff_prof.save()
            return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
